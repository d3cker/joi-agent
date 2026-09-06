# Development and verification

## Working tree

Read root AGENTS.md first. UI code is in apps/macos-client; Python gateway code
is in services/voice-backend. Mutable settings, models and recordings must stay
outside source. Repository documentation is English; language fixtures and
localized resources may contain the language they test.

```sh
make setup
make test-backend
make test-macos
make build
python3 scripts/check_repository.py
```

Python tests require only small core/dev dependencies, not CUDA or models.
Installer tests use temporary directories and fakes for systemd/SSH/GPU calls;
certificate tests execute real OpenSSL. Add tests for retries and rollback when
changing installation order or ownership rules.

SwiftPM/XCTest is the preferred test path. On a Mac with mismatched Command
Line Tools/SDK, scripts/test.sh recognizes a narrow list of toolchain failures
and may run the reduced VoiceAgentCoreSmoke harness. It prints SKIPPED for
XCTest. That result is not the full suite. JOI_STRICT_TESTS=1 forbids fallback;
CI requires the full suite on Apple Silicon and Intel runners. The build script
can use a compatible installed SDK through direct swiftc when SwiftPM fails.

## Focused tests

```sh
.venv/bin/python -m pytest services/voice-backend/tests/test_session.py
.venv/bin/python -m pytest services/voice-backend/tests/test_speech_text.py
.venv/bin/python -m pytest services/voice-backend/tests/test_config_store.py
.venv/bin/python -m pytest installer/tests
```

Run existing tests before deleting an adapter or configuration field. Retire
unused code with an explicit config migration; keep tests for the supported
production path, cancellation, Unicode boundaries and prior user regressions.
Do not fix a test by silently downgrading security, disabling input during TTS,
or accepting incomplete audio.

The remaining deploy/measure_e2e.py and deploy/test_silence_gate.py are reusable
acceptance tools, not historical release scripts. Use --help before a hardware
run. Never put access tokens in shell history or report artifacts. The native
diagnostics are required for claims about physical capture/AEC. See
[speech processing](SPEECH_PIPELINE.md).

## Change validation matrix

| Change | Minimum checks |
| --- | --- |
| Protocol/session | Python session tests + Swift event/state tests + cancel ordering |
| Audio capture | Six-channel/phase/resampling tests + real signed-app PCM recording |
| TTS/renderer | PL/EN fixtures, table/tail coverage, stream boundaries + sampled listening |
| Configuration/security | Revision/conflict/reload tests, no secret leaks, TLS/auth failures |
| Installer | Dry-run purity, cert identity reuse, model reuse, owned-unit rollback |
| Release/build | Strict CI tests, signature, archive extraction, version/checksum manifest |

Keep hardware findings separate from mocks. Never claim a GitHub workflow ran
until its actual run passed; a workflow file alone is not deployment evidence.
