# Joi 1.2.1

## Changes

- Add 110 measurement units to both Polish and English speech packs: 143 units
  and 234 symbol variants per language, including pressure, area, imperial
  measures, volume, energy and electricity.
- Handle Polish quantity agreement and fractional forms for the new units;
  preserve case-sensitive symbols and avoid common prose/ordinal collisions.
- Prevent speech loss when streaming splits a decimal between `1.` and `5 lb`.
- Refresh the session status label immediately on an interface-language change,
  without requiring a connection or session-state transition.
- Add README artwork and include that referenced image in the backend archive.

Speech changes affect TTS-only text, not visible answers or stored history.
Models and wire protocol are unchanged. Clock-time grammar is not addressed
by this release. Automated text tests are not a substitute for listening tests.

## Download and installation

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
