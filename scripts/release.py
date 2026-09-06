#!/usr/bin/env python3
"""Version-independent release packaging. Never packages configuration or models."""
import argparse
import json
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from installer.common import ROOT, archive, digest, product_source_id, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["backend", "macos", "manifest"])
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/release")
    args = parser.parse_args()
    version = (ROOT / "VERSION").read_text().strip()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.kind == "backend":
        archive(args.output / f"Joi-{version}-backend.tar.gz")
    elif args.kind == "macos":
        app = ROOT / "apps/macos-client/dist/VoiceAgent.app"
        with (app / "Contents/Info.plist").open("rb") as stream:
            if plistlib.load(stream)["CFBundleShortVersionString"] != version:
                raise SystemExit("App version does not match VERSION; run make build first")
        subprocess.run(["codesign", "--verify", "--strict", str(app)], check=True)
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app),
            str(args.output / f"Joi-{version}-macOS-{platform.machine()}.zip")], check=True)
    files = sorted(p for p in args.output.iterdir() if p.name.startswith(f"Joi-{version}-") and p.name.endswith((".zip", ".tar.gz")))
    checksums = {p.name: digest(p) for p in files}
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip()
    except subprocess.CalledProcessError:
        revision = "unversioned-source-" + product_source_id()
    component = tomllib.loads((ROOT / "services/voice-backend/pyproject.toml").read_text())["project"]["version"]
    write_json(args.output / "release.json", dict(version=version, revision=revision,
        components={"backend": component, "macos": version}, artifacts=checksums,
        models=json.loads((ROOT / "installer/models.json").read_text())))
    (args.output / "SHA256SUMS").write_text("".join(f"{sha}  {name}\n" for name, sha in checksums.items()))
    print("\n".join(f"{name}  {sha}" for name, sha in checksums.items()))


if __name__ == "__main__":
    main()
