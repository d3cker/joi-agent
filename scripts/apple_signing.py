"""Optional CI-only Apple signing credentials. Never print secret values."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile


def run(args, **kwargs):
    try:
        return subprocess.run(args, check=True, **kwargs)
    except subprocess.CalledProcessError:
        raise SystemExit("Apple tool failed; command arguments omitted to protect credentials") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "notarize", "cleanup"])
    action = parser.parse_args().action
    if not os.environ.get("RUNNER_TEMP") or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Signing helper is restricted to an ephemeral GitHub Actions runner")
    root = Path(os.environ["RUNNER_TEMP"])
    keychain = root / "joi-signing.keychain-db"
    if action == "prepare":
        data = os.getenv("JOI_CERTIFICATE_P12")
        identity = os.getenv("JOI_SIGN_IDENTITY")
        if bool(data) != bool(identity):
            raise SystemExit("Developer ID signing requires both a certificate and its exact identity name")
        if not data:
            print("No Developer ID configured: release will be ad-hoc signed.")
            return
        password = secrets.token_urlsafe(32)
        with tempfile.NamedTemporaryFile(dir=root) as file:
            file.write(base64.b64decode(data, validate=True)); file.flush()
            run(["security", "create-keychain", "-p", password, str(keychain)])
            run(["security", "set-keychain-settings", "-lut", "21600", str(keychain)])
            run(["security", "unlock-keychain", "-p", password, str(keychain)])
            run(["security", "import", file.name, "-k", str(keychain), "-P", os.environ["JOI_CERTIFICATE_PASSWORD"], "-T", "/usr/bin/codesign"])
            run(["security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:", "-s", "-k", password, str(keychain)], stdout=subprocess.DEVNULL)
            run(["security", "list-keychains", "-d", "user", "-s", str(keychain)])
    elif action == "notarize":
        fields = [os.getenv(key) for key in ("JOI_APPLE_ID", "JOI_APPLE_PASSWORD", "JOI_APPLE_TEAM")]
        if not any(fields):
            print("Notarization not configured; see release documentation for Gatekeeper behavior.")
            return
        if not all(fields):
            raise SystemExit("Notarization requires all three Apple credentials")
        app = "apps/macos-client/dist/VoiceAgent.app"
        archive = str(root / "joi-notarize.zip")
        run(["ditto", "-c", "-k", "--keepParent", app, archive])
        result = run(["xcrun", "notarytool", "submit", archive, "--apple-id", fields[0],
            "--password", fields[1], "--team-id", fields[2], "--wait", "--output-format", "json"], capture_output=True, text=True)
        if json.loads(result.stdout).get("status") != "Accepted":
            raise SystemExit("Apple notarization was not accepted; release blocked")
        run(["xcrun", "stapler", "staple", app])
        run(["xcrun", "stapler", "validate", app])
    elif keychain.exists():
        run(["security", "delete-keychain", str(keychain)])


if __name__ == "__main__":
    main()
