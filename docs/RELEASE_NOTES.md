# Joi

Download the archive for your Mac: arm64 for Apple Silicon, x86_64 for Intel.
Unzip VoiceAgent.app and move it to Applications; no compiler is required.
The backend archive includes the local/SSH installer and deployment instructions.

Verify SHA256SUMS before installation. release.json identifies the common
product version, source commit, component versions and model revisions.

Builds are ad-hoc signed unless the repository owner configured Developer ID
signing. Apple notarization is a separate optional pipeline step. An unsigned
or non-notarized download may be blocked by Gatekeeper; only approve it through
Privacy & Security after independently verifying its origin. Do not disable
Gatekeeper or remove quarantine globally.

Install the backend and import its private connection profile as documented
in docs/INSTALLATION.md. Model licenses and voice-recording permissions are
separate from the application's distribution terms.
