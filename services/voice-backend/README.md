# Joi voice backend

FastAPI/asyncio gateway for the native macOS client. Production inference uses
faster-whisper/CTranslate2 for STT, its bundled Silero ONNX for VAD, an external
OpenAI-compatible LLM and an isolated Higgs TTS HTTP service.

Install through root `install.py`, not a historical release-specific script.
See [installation](../../docs/INSTALLATION.md). The installer creates two user
systemd services and private Python environments; it does not install Docker,
SearXNG, OpenTerminal or an LLM server.

For development, run `make setup` and `make test-backend` at repository root.
A disposable mock server can be started without model downloads:

```sh
cd services/voice-backend
JOI_CONFIG_DISABLED=1 VOICE_AGENT_HOST=127.0.0.1 \
VOICE_AGENT_LLM_MODE=mock VOICE_AGENT_STT_MODE=mock \
VOICE_AGENT_VAD_MODE=mock VOICE_AGENT_TTS_MODE=mock \
../../.venv/bin/python -m voice_agent
```

The bypass is for loopback development only, never production. Production
configuration and user-created skills are outside the release under
`~/.config/joi`; sessions live under `~/.local/share/joi`.

See [protocol](../../docs/PROTOCOL.md),
[configuration](../../docs/CONFIGURATION.md) and
[number normalization](../../docs/NUMBER_NORMALIZATION.md).
