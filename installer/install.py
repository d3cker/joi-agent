"""Explicit, idempotent user-service installer. No driver or unrelated service changes."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

from .common import ROOT, archive, atomic_write, digest, source_files, source_id, write_json


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def output(args):
    return run(args, capture_output=True, text=True).stdout.strip()


def checked_path(value: str) -> Path:
    path = Path(value).expanduser().absolute()
    # systemd's %, shell interpolation and control characters cannot enter unit files.
    if any(c in str(path) for c in '\n\r\x00%"\\'):
        raise ValueError("Installation paths may not contain control characters, %, quotes or backslashes")
    return path


def endpoint(ip: str, port: int) -> str:
    address = ipaddress.ip_address(ip)
    return f"wss://{'[' + ip + ']' if address.version == 6 else ip}:{port}/ws"


def plan(args):
    ip = str(ipaddress.ip_address(args.ip))
    if ipaddress.ip_address(ip).is_unspecified or ipaddress.ip_address(ip).is_multicast:
        raise ValueError("Supply the server's reachable IP, not a wildcard or multicast address")
    if not 1 <= args.port <= 65535 or args.port == 8790 or args.gpu < 0:
        raise ValueError("Invalid port/GPU (8790 is reserved for private Higgs)")
    llm = urlsplit(args.llm_url)
    if llm.scheme not in {"http", "https"} or not llm.hostname or llm.username or llm.password or llm.query or llm.fragment:
        raise ValueError("LLM URL must be HTTP(S) without credentials/query; configure API keys separately in Options")
    data = checked_path(args.data_dir or "~/.local/share/joi")
    config = checked_path(args.config_dir or "~/.config/joi")
    version = (ROOT / "VERSION").read_text().strip()
    import re
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("VERSION must be a semantic release version")
    release = data / "releases" / f"{version}-{source_id()[:12]}"
    return dict(ip=ip, port=args.port, gpu=args.gpu, config=str(config), data=str(data), release=str(release),
        version=version, source_sha256=source_id(), endpoint=endpoint(ip, args.port),
        llm_url=args.llm_url, llm_model=args.llm_model,
        voice=str(data / "voices" / Path(args.voice_reference).name),
        transcript=str(data / "voices/reference.txt"),
        stt_model=str(checked_path(args.stt_model_dir) if args.stt_model_dir else data / "models/whisper-large-v3-turbo"),
        tts_model=str(checked_path(args.tts_model_dir) if args.tts_model_dir else data / "models/higgs-tts-3-4b"),
        stt_model_supplied=bool(args.stt_model_dir), tts_model_supplied=bool(args.tts_model_dir))


def certificates(directory: Path, ip: str):
    """Retain the CA and server identity on rerun. Never silently rotate trust."""
    ipaddress.ip_address(ip)
    files = [directory / n for n in ("ca.key", "ca.crt", "server.key", "server.crt")]
    if any(p.exists() for p in files):
        if not all(p.is_file() for p in files):
            raise RuntimeError("Incomplete TLS identity; restore its files before retrying")
        run(["openssl", "verify", "-CAfile", files[1], files[3]], stdout=subprocess.DEVNULL)
        run(["openssl", "x509", "-in", files[3], "-checkip", ip, "-noout"], stdout=subprocess.DEVNULL)
        run(["openssl", "x509", "-in", files[3], "-checkend", "86400", "-noout"], stdout=subprocess.DEVNULL)
        pub_cert = output(["openssl", "x509", "-in", files[3], "-pubkey", "-noout"])
        pub_key = output(["openssl", "pkey", "-in", files[2], "-pubout"])
        if pub_cert != pub_key:
            raise RuntimeError("TLS certificate/private key mismatch")
        for p in (files[0], files[2]):
            p.chmod(0o600)
        return
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        d = Path(tmp)
        common = ["-newkey", "rsa:3072", "-nodes", "-sha256"]
        run(["openssl", "req", "-x509", *common, "-days", "3650", "-subj", "/CN=Joi Private CA",
            "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            "-keyout", d / "ca.key", "-out", d / "ca.crt"], stderr=subprocess.DEVNULL)
        run(["openssl", "req", "-new", *common, "-subj", "/CN=Joi Backend",
            "-keyout", d / "server.key", "-out", d / "server.csr"], stderr=subprocess.DEVNULL)
        atomic_write(d / "extensions", f"subjectAltName=IP:{ip}\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n".encode())
        run(["openssl", "x509", "-req", "-in", d / "server.csr", "-CA", d / "ca.crt", "-CAkey", d / "ca.key",
            "-CAcreateserial", "-days", "3650", "-sha256", "-extfile", d / "extensions", "-out", d / "server.crt"], stderr=subprocess.DEVNULL)
        for target in files:
            atomic_write(target, (d / target.name).read_bytes(), 0o600 if target.suffix == ".key" else 0o644)


def unit(kind: str, p) -> str:
    service = "backend" if kind == "backend" else "higgs"
    root = Path(p["release"])
    return f'''# Managed by Joi installer; do not reuse these names for unrelated services.
[Unit]
Description=Joi {service}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3.12 "{root}/installer/service.py" {service} "{root}/install-plan.json"
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
KillMode=control-group
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
RestrictSUIDSGID=true

[Install]
WantedBy=default.target
'''


def wait_health(url: str, *, ca: Path | None = None, timeout=600):
    context = ssl.create_default_context(cafile=str(ca)) if ca else None
    # Never leak private LAN requests to an inherited HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with opener.open(url, timeout=5) as response:
                data = json.load(response)
                if response.status == 200:
                    return data
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError(f"Service did not become healthy: {url}; inspect journalctl --user -u joi-backend -u joi-tts")


def export_profile(p):
    config = Path(p["config"])
    cert = (config / "security/ca.crt").read_text()
    return dict(schema_version=1, websocket_endpoint=p["endpoint"], ca_certificate_pem=cert,
        ca_sha256=hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert)).hexdigest(),
        client_api_key=(config / "backend/secrets/client-api-key").read_text().strip(),
        config_api_key=(config / "backend/secrets/config-api-key").read_text().strip())


def preflight(args, p):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("Local installation requires Ubuntu 24.04 x86_64; use 'ssh' from macOS")
    if 'VERSION_ID="24.04"' not in Path("/etc/os-release").read_text():
        raise RuntimeError("This installer targets Ubuntu 24.04")
    if os.geteuid() == 0:
        raise RuntimeError("Run as the owning user, not root (sudo is only used with --system-deps)")
    if args.system_deps:
        run(["sudo", "apt-get", "update"])
        run(["sudo", "apt-get", "install", "-y", "python3.12-venv", "python3.12-dev", "build-essential", "openssl", "ninja-build"])
        run(["sudo", "loginctl", "enable-linger", str(os.getuid())])
    for name in ("python3.12", "openssl", "nvidia-smi", "systemctl", "c++", "ninja"):
        if not shutil.which(name):
            raise RuntimeError(f"Missing prerequisite {name}; see docs/INSTALLATION.md")
    if output(["loginctl", "show-user", str(os.getuid()), "-p", "Linger", "--value"]) != "yes":
        raise RuntimeError("User services must survive logout: sudo loginctl enable-linger <your-user>, or --system-deps")
    run(["systemctl", "--user", "show-environment"], stdout=subprocess.DEVNULL)
    gpu = output(["nvidia-smi", f"--id={p['gpu']}", "--query-gpu=driver_version,memory.total,memory.free", "--format=csv,noheader,nounits"])
    driver, total, free = [s.strip() for s in gpu.split(",")]
    if int(driver.split(".")[0]) < 580 or int(total) < 23000:
        raise RuntimeError("Higgs requires the tested driver >=580 and a 24 GiB GPU; installer never upgrades drivers")
    print(f"Selected physical GPU {p['gpu']}: {free}/{total} MiB free; existing processes are never killed")
    units = Path.home() / ".config/systemd/user"
    for name, port in (("joi-backend", p["port"]), ("joi-tts", 8790)):
        path = units / f"{name}.service"
        if path.exists() and not path.read_text().startswith("# Managed by Joi installer;"):
            raise RuntimeError(f"Refusing to overwrite unmanaged {path}")
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                if not path.exists() or output(["systemctl", "--user", "show", name, "-p", "ActiveState", "--value"]) != "active":
                    raise RuntimeError(f"Port {port} is occupied by an unmanaged service; refusing to stop it")
    for value in (args.voice_reference, args.voice_transcript):
        if not Path(value).is_file() or not Path(value).stat().st_size:
            raise RuntimeError(f"Missing/nonempty voice reference or transcript: {value}")
    if shutil.disk_usage(Path.home()).free < 40 * 1024**3:
        raise RuntimeError("At least 40 GiB free disk space is required for model/runtime staging")


def local(args):
    p = plan(args)
    if args.dry_run:
        print(json.dumps(p, indent=2))
        print("PLAN ONLY: Ubuntu checks → isolated venvs → pinned model downloads (~10 GiB plus GPU dependencies) → TLS → user services → health → client profile.")
        return
    os.umask(0o077)
    preflight(args, p)
    import fcntl
    data = Path(p["data"])
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (data / ".install.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another Joi installation is active for this data directory") from None
        _perform_local(args, p)


def _perform_local(args, p):
    release = Path(p["release"])
    current = Path(p["data"]) / "current"
    if current.exists() and not current.is_symlink():
        raise RuntimeError("current is not an installer-owned symlink; refusing to replace it")
    receipt = release / "install-plan.json"
    if receipt.exists() and json.loads(receipt.read_text()) != p:
        raise RuntimeError("Release already has different installation arguments; use its original configuration")
    release.mkdir(parents=True, exist_ok=True)
    for path in source_files():
        target = release / path.relative_to(ROOT)
        if target.exists() and digest(path) != digest(target):
            raise RuntimeError(f"Refusing to overwrite changed release source: {target}")
        if not target.exists():
            atomic_write(target, path.read_bytes(), 0o644)
    write_json(receipt, p)
    for source, target in ((args.voice_reference, p["voice"]), (args.voice_transcript, p["transcript"])):
        target = Path(target)
        if target.exists() and digest(target) != digest(Path(source)):
            raise RuntimeError(f"Existing voice differs at {target}; change it explicitly in configuration")
        if not target.exists():
            atomic_write(target, Path(source).read_bytes())
    for kind in ("backend", "higgs"):
        venv = release / f"venv-{kind}"
        if not (venv / "bin/python").exists():
            run(["python3.12", "-m", "venv", venv])
        marker = release / f"{kind}-dependencies.json"
        if not marker.exists():
            args_pip = [venv / "bin/python", "-m", "pip", "install"]
            if kind == "higgs":
                args_pip.append("--pre")
            args_pip += ["-r", release / f"installer/requirements-{kind}.txt"]
            if kind == "backend":
                args_pip.append(release / "services/voice-backend")
            run(args_pip)
            run([venv / "bin/python", "-m", "pip", "check"])
            write_json(marker, json.loads(output([venv / "bin/python", "-m", "pip", "list", "--format=json"])))
        else:
            run([venv / "bin/python", "-m", "pip", "check"])
    cuda = release / "venv-higgs/lib/python3.12/site-packages/nvidia/cu13"
    for link, target in ((cuda / "lib64", "lib"), (cuda / "lib/libcudart.so", "libcudart.so.13")):
        if not link.exists():
            link.symlink_to(target)
    env = dict(os.environ, JOI_CONFIG_HOME=p["config"], JOI_DATA_HOME=p["data"],
        PYTHONPATH=str(release / "services/voice-backend"),
        CUDA_DEVICE_ORDER="PCI_BUS_ID", CUDA_VISIBLE_DEVICES=str(p["gpu"]),
        LD_LIBRARY_PATH=":".join(str(release / f"venv-backend/lib/python3.12/site-packages/nvidia/{lib}/lib") for lib in ("cublas", "cudnn")))
    helper = [release / "venv-backend/bin/python", release / "installer/provision.py"]
    run([*helper, "models", receipt], env=env)
    certificates(Path(p["config"]) / "security", p["ip"])
    run([*helper, "bootstrap", receipt], env=env)
    # Only after preparation succeeds do we replace our own services.
    units = Path.home() / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    names = {"joi-tts": "higgs", "joi-backend": "backend"}
    backups = {name: (units / f"{name}.service").read_bytes() if (units / f"{name}.service").exists() else None for name in names}
    active = {name: subprocess.run(["systemctl", "--user", "is-active", "--quiet", name]).returncode == 0 for name in names}
    enabled = {name: subprocess.run(["systemctl", "--user", "is-enabled", "--quiet", name]).returncode == 0 for name in names}
    try:
        for name, kind in names.items():
            atomic_write(units / f"{name}.service", unit(kind, p).encode(), 0o644)
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "restart", "joi-tts"])
        wait_health("http://127.0.0.1:8790/health")
        run([*helper, "ack", receipt], env=env)
        run([*helper, "probe", receipt], env=env)
        run(["systemctl", "--user", "restart", "joi-backend"])
        health = wait_health(p["endpoint"].replace("wss:", "https:").replace("/ws", "/health"), ca=Path(p["config"]) / "security/ca.crt")
        run(["systemctl", "--user", "enable", "joi-backend", "joi-tts"])
    except BaseException:
        run(["systemctl", "--user", "stop", *names])
        for name, was_enabled in enabled.items():
            if not was_enabled:
                run(["systemctl", "--user", "disable", name])
        for name, data in backups.items():
            target = units / f"{name}.service"
            if data is None:
                target.unlink(missing_ok=True)  # Only the unit we just created.
            else:
                atomic_write(target, data, 0o644)
        run(["systemctl", "--user", "daemon-reload"])
        for name, was_active in active.items():
            if was_active:
                run(["systemctl", "--user", "start", name])
        raise
    link = current.with_name(".current-next")
    link.unlink(missing_ok=True)
    link.symlink_to(release)
    os.replace(link, current)
    profile_path = Path(p["config"]) / "client-connection.json"
    write_json(profile_path, export_profile(p))
    write_json(release / "health-receipt.json", health)
    print(f"Installed {p['version']}: {release}\nClient WebSocket: {p['endpoint']}")
    print(f"CA: {p['config']}/security/ca.crt\nCA SHA256: {export_profile(p)['ca_sha256']}")
    print(f"Client/admin keys: {p['config']}/backend/secrets/{{client-api-key,config-api-key}}")
    print(f"Private client profile: {profile_path}\nCopy it securely to the Mac; no client configuration is written on Ubuntu.")


def ssh(args):
    # Remote path fixed relative to authenticated user's home. No passwords in argv.
    import re
    if not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+", args.host):
        raise ValueError("SSH host must be user@host (configure alternate ports through ~/.ssh/config)")
    if args.config_dir or args.data_dir:
        raise ValueError("SSH mode uses the target user's standard Joi paths; custom paths are local-mode only")
    connection = ["ssh", "-o", "StrictHostKeyChecking=yes"]
    if args.control_path:
        connection += ["-o", f"ControlPath={checked_path(args.control_path)}"]
    connection += [args.host]
    remote = ".cache/joi-installer/" + source_id()[:12]
    forward = ["python3", f"{remote}/install.py", "local", "--ip", args.ip,
        "--port", str(args.port), "--gpu", str(args.gpu), "--llm-url", args.llm_url,
        "--llm-model", args.llm_model, "--voice-reference", f"{remote}/reference{Path(args.voice_reference).suffix}",
        "--voice-transcript", f"{remote}/reference.txt"]
    for flag, val in (("--stt-model-dir", args.stt_model_dir), ("--tts-model-dir", args.tts_model_dir)):
        if val:
            forward += [flag, val]
    if args.system_deps:
        forward.append("--system-deps")
    if args.dry_run:
        print(f"SSH {args.host}: transfer allowlisted source and supplied voice; run {shlex.join(forward)}")
        print("Then retrieve private client profile over SSH; optionally import with the signed Mac app.")
        return
    with tempfile.TemporaryDirectory(prefix="joi-transfer-") as tmp:
        tar = Path(tmp) / "source.tar.gz"
        archive(tar)
        # Local archive has only relative regular files from our allowlist.
        with tar.open("rb") as stream:
            run([*connection, f"umask 077; mkdir -p {shlex.quote(remote)} && tar -xzf - -C {shlex.quote(remote)}"], stdin=stream)
        for source, target in ((args.voice_reference, forward[forward.index("--voice-reference")+1]),
                               (args.voice_transcript, forward[forward.index("--voice-transcript")+1])):
            with Path(source).open("rb") as stream:
                run([*connection, f"umask 077; cat > {shlex.quote(target)}"], stdin=stream)
        run([*connection[:-1], "-t", connection[-1], shlex.join(forward)])
        profile = json.loads(output([*connection, "cat .config/joi/client-connection.json"]))
        target = checked_path(args.client_profile or "~/.config/joi/imports/server.connection.json")
        write_json(target, profile)
        print(f"Private client profile saved: {target}")
        if args.client_app:
            import_client(target, checked_path(args.client_app))


def import_client(profile: Path, app: Path):
    if platform.system() != "Darwin":
        raise RuntimeError("Client import requires macOS")
    run([app / "Contents/MacOS/VoiceAgent", "--import-connection", profile])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("local", "ssh"):
        sub = modes.add_parser(mode)
        sub.add_argument("--ip", required=True, help="Reachable backend IP; encoded into the server certificate")
        sub.add_argument("--port", type=int, default=8765)
        sub.add_argument("--gpu", type=int, default=2, help="One physical GPU (default 2); never auto-select occupied GPUs")
        sub.add_argument("--llm-url", required=True)
        sub.add_argument("--llm-model", required=True)
        sub.add_argument("--voice-reference", required=True, help="User-supplied consenting speaker WAV/FLAC")
        sub.add_argument("--voice-transcript", required=True, help="UTF-8 file with exact reference transcript")
        sub.add_argument("--stt-model-dir", help="Existing model path ON THE TARGET; otherwise download pinned revision")
        sub.add_argument("--tts-model-dir", help="Existing model path ON THE TARGET; otherwise download pinned revision")
        sub.add_argument("--data-dir")
        sub.add_argument("--config-dir")
        sub.add_argument("--dry-run", action="store_true")
        sub.add_argument("--system-deps", action="store_true", help="Explicitly allow sudo apt prerequisites and user linger; never GPU drivers")
        if mode == "ssh":
            sub.add_argument("--host", required=True)
            sub.add_argument("--control-path")
            sub.add_argument("--client-profile")
            sub.add_argument("--client-app", help="Optional signed VoiceAgent.app to import profile locally")
    client = modes.add_parser("client")
    client.add_argument("--profile", required=True, type=Path)
    client.add_argument("--app", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "local":
            local(args)
        elif args.mode == "ssh":
            ssh(args)
        else:
            import_client(args.profile.expanduser(), args.app.expanduser())
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Installation stopped: {exc}\nNo unrelated services have been stopped.\n")
