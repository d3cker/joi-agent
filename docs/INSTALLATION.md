# Installation

## Supported first-install target

- Ubuntu 24.04 x86_64, Python 3.12 and a normal non-root user.
- NVIDIA driver 580 or newer and a 24 GiB GPU; the development card is RTX 3090.
  Choose one physical GPU explicitly. Other processes are never evicted.
- At least 40 GiB free disk space for weights, private environments and staging.
- Existing reachable OpenAI-compatible Chat Completions endpoint/model.
- A WAV/FLAC voice reference you have permission to use and a UTF-8 file with
  its exact transcript. These files are not distributed with Joi.
- OpenSSL, a C++ compiler, Ninja, Python venv support and a user systemd manager.
  User linger must be enabled for operation after SSH logout/reboot.

The installer does **not** install drivers, an LLM server, Docker, SearXNG or
OpenTerminal. It does not modify another Python environment or system CUDA.
The backend and Higgs get separate per-release venvs. Whisper uses packaged
CUDA 12 libraries; Higgs uses its private CUDA 13.0 compiler toolchain. Models
are stored outside releases and reused.

New model downloads total roughly 10 GiB, plus several GiB of GPU dependencies.
Exact sizes depend on platform resolution. installer/models.json pins model
revisions; Higgs weights additionally have a SHA-256 check. Requirement files
pin serving/STT/compiler releases, while transitive package resolution is
recorded in the installation receipts. This is not yet a full hash-locked GPU
wheelhouse. A resolver or ABI failure stops installation; never use --no-deps
or ignore pip check to force a broken environment into production.

## Local mode — run on Ubuntu

From a clone or extracted backend package:

```sh
python3 install.py local \
  --ip 192.0.2.15 --gpu 2 \
  --llm-url http://192.0.2.16:8888/v1 --llm-model your-model \
  --voice-reference /path/to/reference.wav \
  --voice-transcript /path/to/reference.txt --dry-run
```

The example addresses are documentation placeholders; replace them with your
actual LAN addresses. Remove --dry-run after reviewing the plan. Dry-run does
not contact the host, create directories, install packages or download models.

If required, add --system-deps to explicitly authorize sudo for apt build
prerequisites and loginctl enable-linger. Otherwise install those beforehand:

```sh
sudo apt-get install python3.12-venv python3.12-dev build-essential openssl ninja-build
sudo loginctl enable-linger YOUR_USER
```

Never run the whole installer with sudo. If systemctl --user fails in an SSH
session, establish a normal login with a working user manager first.

Existing weights can be selected without downloading replacements:

```sh
# Add to either local or SSH installation arguments:
--stt-model-dir /absolute/target/path/whisper-large-v3-turbo \
--tts-model-dir /absolute/target/path/higgs-tts-3-4b
```

Supplied model folders are checked without being overwritten. For STT the
installer validates required files; for Higgs it also validates the pinned
weight checksum. User-supplied STT provenance is not claimed to be verified.

Local mode always treats the machine running the command as the backend.
This includes running it after manually logging in with SSH. It displays the
endpoint, CA fingerprint and the locations of client/admin keys, and writes a
private client-connection.json handoff. It **does not change Mac preferences**.

## SSH mode — run on the Mac

The installer itself requires Python 3.11 or newer on the Mac. The downloaded
native app does not require Python. For a client-only setup after local Ubuntu
installation, you can invoke the app's --import-connection command directly.

Establish and verify the SSH host key first with your normal ssh command.
The installer uses StrictHostKeyChecking=yes and supports your SSH agent,
interactive password authentication and an optional existing ControlPath.
Passwords are never placed in command arguments or stored by the installer.

```sh
python3 install.py ssh --host your-user@192.0.2.15 \
  --ip 192.0.2.15 --gpu 2 \
  --llm-url http://192.0.2.16:8888/v1 --llm-model your-model \
  --voice-reference /path/on/your/Mac/reference.wav \
  --voice-transcript /path/on/your/Mac/reference.txt \
  --client-app /Applications/VoiceAgent.app --dry-run
```

Remove --dry-run to transfer allowlisted source and the two reference files,
then execute the same local installer remotely. Existing-model paths refer to
the **remote** host; reference input paths refer to the **Mac**. Custom data and
configuration roots are local-mode options only. Use ~/.ssh/config for port,
identity and ProxyJump settings; --host accepts user@host.

The private connection profile is retrieved over SSH and written locally to
~/.config/joi/imports/server.connection.json (override with --client-profile).
With --client-app, the installer invokes that signed app's import command.
Without it, the handoff is ready for later import:

```sh
python3 install.py client \
  --profile ~/.config/joi/imports/server.connection.json \
  --app /Applications/VoiceAgent.app
```

macOS may ask for trust/Keychain approval. Import verifies the CA fingerprint,
adds user SSL trust, stores keys in Keychain and changes only the endpoint in
client preferences. Restart an already running app. Never send the server's
ca.key or server.key to a client. A connection profile is a secret-bearing
administrative artifact; keep it private and do not attach it to bug reports.

For manual setup, securely copy ca.crt to the Mac and trust it in login Keychain,
enter the displayed WSS endpoint in Options, then copy client-api-key and
config-api-key into their respective access fields. Their server locations are
printed by the installer; secret contents are deliberately not printed to logs.

## Services, retries and rollback

The installer owns only these user services:

```sh
systemctl --user status joi-backend joi-tts
journalctl --user -u joi-backend -u joi-tts -n 100
systemctl --user restart joi-backend
```

The backend listens with TLS on the configured port (default 8765). Higgs
listens only on 127.0.0.1:8790. Open the backend port in your existing network
firewall yourself; the installer does not change firewall rules.

Source is staged under ~/.local/share/joi/releases/VERSION-SOURCEHASH.
Settings, sessions, voices and weights are outside the release. Identical retries
reuse completed environments, certificates, voices and model caches. Changed
source receives a new release path; it does not overwrite the previous one.
Existing configuration is authoritative, including changes saved in Options.
Conflicting installation arguments or an incomplete identity fail explicitly.

Preparation must succeed before owned units are switched. A concurrent install
for the same data directory is rejected by an exclusive lock. The previous unit
contents/active states are retained; failed readiness restores them. Readiness
includes generating/reusing the ack and passing it through real VAD and GPU STT,
not merely checking an HTTP listener. This still is not a microphone test. Initial
failure leaves no new running Joi services. Staged files remain for diagnosis
and retry. The current symlink changes only after both services are healthy.
Already-created configuration is retained, not deleted on failure. Back up
configuration before upgrading: release rollback is not a database/schema
downgrade mechanism. The installer refuses to take over an unmanaged production
listener (including the historical development deployment).

To manually roll back, use the previous release's saved install-plan.json and
the installer.unit function to regenerate its two unit definitions, then
daemon-reload/restart **only Joi**. Preserve the existing persistent configuration
or restore a compatible backup. Do not select a prior release with an
incompatible schema. No model downloads or other GPU-service changes are needed.

## Acceptance checklist

1. Inspect install receipts and journal: selected physical GPU, no dependency
   conflicts, complete model files, both services active, no pending requests.
2. Verify HTTPS /health using the generated CA, not an insecure -k bypass.
3. Verify anonymous session/config requests and WebSocket handshakes are denied.
4. Import profile into the app and confirm session listing/config reads.
5. Run the local microphone meter and five-second PCM recording; listen to it.
6. Test automatic speech commit, multi-segment output and real spoken barge-in.
7. Test English and Polish numbers/Markdown, then check GPU use after idle.
8. Log out of SSH and confirm the user services remain available.

The repository tests exercise installer control flow, real certificate creation
and failure cases without downloading GPU dependencies. A clean Ubuntu GPU
installation still requires the above acceptance run; successful packaging is
not evidence that this hardware test has happened.
