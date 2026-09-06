# Joi development instructions

This file governs work on this repository. Read it completely before editing.
It is developer guidance, **not** the runtime assistant prompt. Bundled
SKILL.md files under services/voice-backend/skills describe the deployed
assistant's tool workflows and do not prohibit normal developer tools here.

## Product and non-negotiable boundaries

Joi is a native macOS client for a network-hosted voice agent. It is not a web
app and does not require inference on the Mac. The tested topology is an Ubuntu
24.04 host with RTX 3090 speech inference and an external DeepSeek V4 Flash LLM
on two DGX Sparks. The installer must also support another explicitly supplied
IP, model endpoint and single physical GPU without source edits.

- Preserve existing user changes and mutable data. Never use destructive Git
  resets, delete a broad directory, or overwrite an unknown running app/service.
- Never stop/reconfigure vLLM, Qwen, ComfyUI or another user's GPU process.
  On the development host physical GPU 0/1 are reserved; speech uses GPU 2.
  Inside CUDA_VISIBLE_DEVICES, logical device 0 is the selected physical card.
- No Docker assumption. No NVIDIA driver/system CUDA changes. No system-wide
  Python installs. Package/model downloads require task authorization.
- SearXNG and OpenTerminal are external services. Do not install substitutes.
  The runtime agent's arbitrary code/file tools operate **only** in OpenTerminal.
  The bounded skill importer is the sole deliberate backend file-publication
  workflow; it is not a generic shell or filesystem tool.
- HTTPS/WSS for network client traffic, direct backend TLS, IP SAN certificates.
  Cleartext is loopback-only. No accept-all certificate delegate or insecure
  TLS fallback. Keep client and configuration-admin keys distinct.
- Never write secrets, private recordings, model weights, generated settings or
  private certificates into source, tool reports or release archives.
- All repository Markdown prose is English. Polish/other language examples in
  tests and language documentation are legitimate fixtures, not UI hardcoding.
- Do not publish, tag, deploy to production or restart services unless the
  current task authorizes that operation. Building locally is not deployment.

## Read the relevant topic documents

| Work area | Required reference |
| --- | --- |
| First orientation/process boundaries | docs/ARCHITECTURE.md |
| Capture/VAD/STT/TTS/interruptions | docs/SPEECH_PIPELINE.md |
| Number/locale/rendering changes | docs/NUMBER_NORMALIZATION.md |
| HTTP/WS payloads and ordering | docs/PROTOCOL.md |
| Persistent configuration, prompts, skills | docs/CONFIGURATION.md |
| TLS, access control, installation trust | docs/SECURITY.md |
| Installer/deployment/rollback | docs/INSTALLATION.md |
| Test expectations and hardware evidence | docs/DEVELOPMENT.md |
| Packaging, versions, Actions/signing | docs/RELEASES.md |

Read the source for exact current schemas; prose must not override working
contracts. Update the topic document and tests alongside a behavior change.

## Code map

### Native client: apps/macos-client

- Package.swift: VoiceAgentCore library, VoiceAgent executable and XCTest.
- Sources/VoiceAgent/VoiceAgentApp.swift: native app/windows, explicit installer
  import entry point. Keep the bundle identity stable for microphone TCC and
  Keychain continuity.
- ConversationStore.swift: MainActor coordination of sessions, preferences,
  WebSocket events and playback. Publish UI/counters on the correct actor.
- AudioController.swift: actual AVAudioEngine/device capture and playback.
  Inspect the actual device/buffer format before making conversion assumptions.
- ContentView.swift and SettingsView.swift: primary UI, diagnostics and options.
  Do not put transport endpoints back into the main conversation screen.
- ConfigKeychain.swift and ConnectionImport.swift: credentials and trusted
  installer handoff, never include secrets in diagnostic copies.
- Sources/VoiceAgentCore: deterministic state machines, capture policy,
  resampling/channel selection, Markdown representation, configuration and
  transport contracts. Keep this layer testable without physical hardware.
- Resources/Localization: catalog plus language strings. A language selector
  must use the catalog, not a fixed ENG/POL toggle. New UI strings belong here.
- AppBundle: Info.plist and signing metadata. Root VERSION overrides bundle
  release numbers at build time; do not hardcode a new version in scripts.
- Tests/VoiceAgentCoreTests: full XCTest suite. VoiceAgentCoreSmoke is a reduced
  local CLT fallback, never a replacement for strict CI.

### Gateway: services/voice-backend/voice_agent

- app.py / __main__.py: FastAPI routes, dependency lifecycle, authentication,
  native TLS startup and supervised restart. Check route protection when adding
  endpoints. Public health is intentionally limited and unauthenticated.
- config.py / config_store.py: typed defaults, validation, section schema,
  atomic persistent documents, secret files and migration. Update every map
  when adding/removing a field. Preserve existing configuration on upgrade.
- protocol.py: command parsing and event constructors. Evolve optional fields
  compatibly; update Swift decoding and fixture tests in the same change.
- session.py: utterance lifecycle, prompt/tool loop, streaming, response-scoped
  cancellation, ordered TTS worker and telemetry. Read its existing regression
  tests before changing concurrency.
- endpointing.py / stt.py: Silero state, silence/energy gate and Whisper child
  process. Whisper must transcribe the active language, not translate it.
- llm.py: OpenAI-compatible SSE, separate reasoning/content, tool-call deltas
  and usage. Reasoning must never be forwarded as spoken assistant content.
- tts.py / ack.py: HTTP Higgs adapter, voice profiles and preloaded ack. The
  retired Chatterbox path/pronunciation dictionary must not be reintroduced.
- speech_text.py, number_normalization.py, speech_semantics.py,
  structured_values.py, polish_numbers.py and english_numbers.py: speech
  rendering and language grammar; streaming segmentation is in session.py.
  Shared recognition precedes locale grammar.
  Keep visible Markdown untouched; normalization is TTS-only.
- storage.py / skills.py / prompts.py / tools.py / remote_tools.py: inspect
  their APIs before changes. Session storage is SQLite; skills use a persistent
  manifest and explicit validated import/reload. Never infer that a draft in
  OpenTerminal is already an active backend skill.
- prompts/default-system.md, skills/*/SKILL.md: bundled install defaults copied
  only when missing. Existing user instructions are not reset by deployments.
- tests: executable regressions including prior real-user failures. Retain
  fixture coverage, even when removing obsolete implementation branches.
- deploy/measure_e2e.py and test_silence_gate.py: reusable hardware acceptance
  tools; no version-specific packaging/deploy scripts belong here.

### Installation and release

- install.py: the single CLI entry point, modes local / ssh / client.
- installer/install.py: explicit preflight, private staging, certificate
  generation, owned user units, readiness, rollback and SSH orchestration.
- installer/provision.py: backend-venv model checks/bootstrap/ack generation.
- installer/service.py: fixed process launcher with private CUDA environment;
  must exec the service so systemd owns its whole process group.
- installer/models.json: immutable model provenance, required files/checksums.
- installer/requirements-*.txt: separate GPU environments and compiler pins.
  Do not claim these are a complete hash-locked dependency graph. Record resolved
  versions and fail on dependency conflicts; never force --no-deps around ABI.
- installer/common.py: archive allowlist and private atomic writes. Any new
  source directory needed by deployment must be explicitly allowed and tested.
- scripts/release.py: common-version artifacts/manifests/checksums.
- scripts/apple_signing.py: ephemeral CI credentials, never print subprocess
  arguments containing passwords, including exception paths.
- scripts/check_repository.py: layout/documentation/secret-marker checks.
- .github/workflows: strict tests, native ARM/Intel builds and tag release.
  Only the publish job needs contents:write. Never expose secrets to untrusted PRs.

## Critical behavior invariants

1. Capture per-channel levels before mono selection. Channel 0 may be silent
   in a 48 kHz six-channel non-interleaved device. No phase-cancelling sum.
2. Final wire input is PCM16 little-endian mono 16 kHz with continuous state.
   Diagnostic WAV records that exact output. Separate callbacks, converted
   frames and successfully sent frames/bytes; avoid off-actor counter races.
3. Confirm speech and energy before STT. A manual commit of silence reports
   no speech rather than generating a guessed transcript.
4. Real spoken barge-in stays enabled. Do not disable the microphone during TTS
   to hide echo bugs. Physical AEC claims require actual microphone testing.
5. Consume/forward LLM SSE independently of TTS inference. A single synthesis
   worker preserves order. Queue saturation must not silently drop text/audio.
6. assistant.done is text completion, not playback completion. Every audio
   marker/binary pair is atomic; response IDs and indices remain ordered.
7. Cancellation cleans the response task/worker/queue and rejects stale audio.
   It must not kill unrelated background tools or other sessions' work.
8. Render Markdown structurally, including multiline table cells and trailing
   prose. Speech renderer consumes all relevant content, not only headers.
9. Normalize by the active language pack: abbreviations, grouping, dates,
   units, signs, IP/CIDR, code identifiers and paths. Never add exceptions for
   one reported price/date/brand; add precedence/grammar fixtures for PL and EN.
10. LLM t/s measures model generation only; use usage tokens when available and
    label estimates otherwise. Report TTS latency and queue depth separately.
11. Settings saves are revisioned and persistent. Language/model changes must
    not silently reset user sessions, keys or skills. GPU selection is read-only
    remotely and must remain consistent with the service launcher.

## Required workflow and handoff

1. Inspect git status if Git is present; never assume this workspace has a remote.
2. Identify the smallest coherent change and read the relevant source/docs.
3. Add regression tests, including failure/cancellation/retry paths.
4. Run make setup, targeted tests, make test-backend and applicable macOS tests.
5. Run make build after Swift changes; verify codesign and extracted archive.
6. Run python3 scripts/check_repository.py and inspect package contents.
7. For installer changes, exercise dry-run and mocked rollback. A clean Ubuntu
   GPU install/model download is a separate authorized acceptance step.
8. For speech changes, distinguish synthetic, file-based E2E and actual native
   microphone evidence. Never claim the latter from a generated PCM fixture.
9. Before release, update root VERSION and component metadata intentionally;
   rebuild checksums after every artifact change. Keep topic docs in English.
10. Report outcome, exact tested scope, artifact paths and remaining blockers.
    Do not call a workflow published, production switched or hardware verified
    without corresponding evidence. Preserve a recoverable previous release.
