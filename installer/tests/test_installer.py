import argparse
import json
from pathlib import Path
import ssl
import subprocess
import tarfile

import pytest

from installer.common import ROOT, archive, atomic_write, source_files
from installer.install import certificates, endpoint, plan, unit, local, export_profile, checked_path


def args(tmp_path, **overrides):
    defaults = dict(ip="192.0.2.15", port=8765, gpu=2,
        data_dir=str(tmp_path / "data"), config_dir=str(tmp_path / "config"),
        voice_reference="reference.wav", voice_transcript="reference.txt",
        llm_url="http://192.0.2.16:8888/v1", llm_model="test-model",
        stt_model_dir=None, tts_model_dir=None, dry_run=True)
    return argparse.Namespace(**(defaults | overrides))


def test_dry_run_has_no_side_effects(tmp_path, capsys):
    local(args(tmp_path))
    assert list(tmp_path.iterdir()) == []
    assert "PLAN ONLY" in capsys.readouterr().out


@pytest.mark.parametrize("ip", ["0.0.0.0", "::", "224.0.0.1", "example.com", "127.0.0.1\nExecStart=bad"])
def test_invalid_ip_rejected(tmp_path, ip):
    with pytest.raises(ValueError):
        plan(args(tmp_path, ip=ip))


def test_ipv6_endpoint_and_unit_quoting(tmp_path):
    assert endpoint("2001:db8::1", 8765) == "wss://[2001:db8::1]:8765/ws"
    p = plan(args(tmp_path, data_dir=str(tmp_path / "space here")))
    text = unit("higgs", p)
    assert '"' + p["release"] + '/installer/service.py" higgs' in text
    assert "KillMode=control-group" in text
    assert "Qwen" not in text and "ComfyUI" not in text


@pytest.mark.parametrize("value", ["/tmp/evil\nline", "/tmp/%h", '/tmp/"path'])
def test_unsafe_unit_paths(value):
    with pytest.raises(ValueError):
        checked_path(value)


def test_archive_is_allowlisted_and_contains_installer(tmp_path):
    target = tmp_path / "backend.tar.gz"
    archive(target)
    second = tmp_path / "second.tar.gz"
    archive(second)
    assert target.read_bytes() == second.read_bytes()
    with tarfile.open(target) as tar:
        names = tar.getnames()
        assert "install.py" in names and "installer/models.json" in names
        assert "services/voice-backend/voice_agent/app.py" in names
        assert all(item.isfile() for item in tar.getmembers())
        assert all(not name.startswith(("/", "outputs/", "work/")) for name in names)
        assert all(not name.endswith((".key", ".wav", ".flac", ".pyc")) for name in names)
        assert all(".." not in Path(name).parts for name in names)
        assert all(not any(part.startswith(".") or part.endswith(".egg-info") for part in Path(name).parts) for name in names)


def test_extracted_archive_runs_without_original_checkout(tmp_path):
    import sys
    target = tmp_path / "backend.tar.gz"
    archive(target)
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(target) as tar:
        tar.extractall(extracted, filter="data")
    result = subprocess.run([sys.executable, str(extracted / "install.py"), "local",
        "--ip", "192.0.2.15", "--llm-url", "http://192.0.2.16:8888/v1", "--llm-model", "test",
        "--voice-reference", "nonexistent.wav", "--voice-transcript", "nonexistent.txt", "--dry-run"],
        cwd=tmp_path, capture_output=True, text=True, check=True)
    assert "PLAN ONLY" in result.stdout
    assert not (extracted / "venv-backend").exists()


def test_ssh_handoff_is_private_and_uses_verified_transport(tmp_path, monkeypatch, capsys):
    import installer.install as module
    a = args(tmp_path)
    a.host = "user@192.0.2.15"
    a.config_dir = a.data_dir = None
    a.control_path = None
    a.system_deps = False
    a.client_profile = str(tmp_path / "server.connection.json")
    a.client_app = None
    a.dry_run = False
    a.voice_reference = str(tmp_path / "voice.wav")
    a.voice_transcript = str(tmp_path / "voice.txt")
    Path(a.voice_reference).write_bytes(b"test voice")
    Path(a.voice_transcript).write_text("voice transcript")
    calls = []
    monkeypatch.setattr(module, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(module, "output", lambda cmd: json.dumps({"schema_version": 1, "client_api_key": "never-log-this-key"}))
    module.ssh(a)
    assert Path(a.client_profile).stat().st_mode & 0o777 == 0o600
    assert all("StrictHostKeyChecking=yes" in cmd for cmd in calls)
    assert "never-log-this-key" not in capsys.readouterr().out
    assert all("password" not in " ".join(map(str, cmd)) for cmd in calls)


def test_private_atomic_write(tmp_path):
    target = tmp_path / "secret"
    atomic_write(target, b"first")
    atomic_write(target, b"second")
    assert target.read_bytes() == b"second"
    assert target.stat().st_mode & 0o777 == 0o600


def test_source_allowlist_excludes_named_venvs_and_secrets(tmp_path):
    root = tmp_path / "repo"
    backend = root / "services/voice-backend"
    for name in ("voice_agent/app.py", ".venv-old/lib/settings.json", ".env", "config/server.key", "runtime/secret.json"):
        file = backend / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture")
    assert [str(p.relative_to(root)) for p in source_files(root)] == ["services/voice-backend/voice_agent/app.py"]


@pytest.mark.parametrize("ip,other_ip", [
    ("192.0.2.15", "192.0.2.16"),
    ("2001:db8::15", "2001:db8::16"),
])
def test_real_certificate_generation_idempotence_and_profile(tmp_path, ip, other_ip):
    directory = tmp_path / "security"
    certificates(directory, ip)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    certificates(directory, ip)
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
    ssl.create_default_context(cafile=str(directory / "ca.crt"))
    with pytest.raises(subprocess.CalledProcessError):
        certificates(directory, other_ip)
    secrets = tmp_path / "backend/secrets"
    atomic_write(secrets / "client-api-key", b"client-key")
    atomic_write(secrets / "config-api-key", b"admin-key")
    profile = export_profile(dict(config=str(tmp_path), endpoint=endpoint(ip, 8765)))
    assert len(profile["ca_sha256"]) == 64
    assert "PRIVATE KEY" not in json.dumps(profile)
    assert directory.joinpath("server.key").stat().st_mode & 0o777 == 0o600


def test_partial_tls_identity_fails_closed(tmp_path):
    atomic_write(tmp_path / "ca.key", b"existing")
    with pytest.raises(RuntimeError, match="Incomplete TLS"):
        certificates(tmp_path, "192.0.2.15")
    assert (tmp_path / "ca.key").read_bytes() == b"existing"


def test_failed_service_activation_restores_only_owned_units(tmp_path, monkeypatch):
    import installer.install as module
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    a = args(tmp_path, dry_run=False)
    a.voice_reference = str(tmp_path / "reference.wav")
    a.voice_transcript = str(tmp_path / "reference.txt")
    Path(a.voice_reference).write_bytes(b"fixture voice")
    Path(a.voice_transcript).write_text("fixture transcript")
    monkeypatch.setattr(module, "preflight", lambda *_: None)
    calls = []

    def fake_run(command, **kwargs):
        cmd = [str(v) for v in command]
        calls.append(cmd)
        if cmd[:3] == ["python3.12", "-m", "venv"]:
            root = Path(cmd[3])
            (root / "bin").mkdir(parents=True)
            (root / "bin/python").touch()
            (root / "lib/python3.12/site-packages/nvidia/cu13/lib").mkdir(parents=True)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(module, "run", fake_run)
    monkeypatch.setattr(module, "output", lambda *_: "[]")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module, "certificates", lambda *_: None)
    def fail_health(*args, **kwargs):
        raise RuntimeError("fixture failed readiness")
    monkeypatch.setattr(module, "wait_health", fail_health)
    units = tmp_path / ".config/systemd/user"
    units.mkdir(parents=True)
    old = b"# Managed by Joi installer; previous release\n"
    for name in ("joi-backend", "joi-tts"):
        (units / f"{name}.service").write_bytes(old)
    unrelated = units / "unrelated.service"
    unrelated.write_text("must survive")
    with pytest.raises(RuntimeError, match="fixture failed readiness"):
        local(a)
    assert unrelated.read_text() == "must survive"
    assert all((units / f"{name}.service").read_bytes() == old for name in ("joi-backend", "joi-tts"))
    assert not (tmp_path / "data/current").exists()
    stopped = [c for c in calls if c[:3] == ["systemctl", "--user", "stop"]]
    assert stopped == [["systemctl", "--user", "stop", "joi-tts", "joi-backend"]]


def test_supplied_models_are_not_downloaded(tmp_path, monkeypatch):
    import sys
    import types
    from installer import provision
    model_root = tmp_path / "models"
    model_root.mkdir()
    (model_root / "model.bin").write_bytes(b"fixture")
    # Catalog fixture tests control flow, not real model validity.
    root = tmp_path / "source"
    (root / "installer").mkdir(parents=True)
    (root / "installer/models.json").write_text(json.dumps({"stt": {"repository": "test/repo", "revision": "abc", "files": ["model.bin"]}}))
    monkeypatch.setattr(provision, "ROOT", root)
    def no_download(*args, **kwargs):
        pytest.fail("Existing model must not be downloaded")
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=no_download))
    provision.models(dict(stt_model=str(model_root), stt_model_supplied=True, release=str(tmp_path / "release")))
    assert (tmp_path / "release/stt-model-receipt.json").exists()
