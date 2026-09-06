# Joi — a network-first voice agent

![Joi — a holographic companion in a neon-lit city](img/joi.png)

Joi is a native macOS voice client connected to a self-hosted Ubuntu speech
backend. Models do not need to run on your Mac: the application is designed for
a private network, with an independently hosted OpenAI-compatible LLM.

The development setup uses one **RTX 3090** for Whisper large-v3-turbo STT,
Silero ONNX voice detection and **Higgs TTS 3 4B** speech generation. The LLM is
**DeepSeek V4 Flash 0731 on two DGX Spark machines**. 

- Natural end-of-speech detection, sentence-level speech and interruption.
- Persistent sessions, Markdown tables, tool activity and context telemetry.
- English/Polish interface and speech normalization.
- Skills and system prompts in editable Markdown.
- Native HTTPS/WSS with an IP certificate, client access key and separate
  configuration-management key; no reverse proxy, DNS or Bonjour required.
- Optional tools use **your existing SearXNG and OpenTerminal**. Joi installs
  neither; arbitrary agent execution occurs only in OpenTerminal.

## Install

Download the macOS app and backend archive from this repository's GitHub Releases.
Unzip the app; copy it to Applications. See [Installation](docs/INSTALLATION.md)
for prerequisites, certificate trust and a complete first-run checklist.

One installer supports both deployment locations:

```sh
# Run on Ubuntu, or use "ssh --host user@server-ip" from your Mac.
python3 install.py local --ip SERVER_IP --gpu 2 \
  --llm-url http://LLM_IP:8888/v1 --llm-model YOUR_MODEL \
  --voice-reference /path/to/reference.wav \
  --voice-transcript /path/to/reference.txt --dry-run
```

Remove `--dry-run` to install. New installations download several GiB of
models and GPU dependencies. Existing local model directories can be reused.
Supply a voice recording you have permission to use and its exact transcript.

Configuration and skills: `~/.config/joi` on each machine. Backend runtime,
models and sessions: `~/.local/share/joi`. These are separate filesystems.

## Develop

```sh
make setup            # Small Python test environment; no model downloads
make test             # Backend/installer tests + macOS tests
make build            # apps/macos-client/dist/VoiceAgent.app
make package          # Versioned archives and SHA256SUMS in outputs/release/
```

Read [AGENTS.md](AGENTS.md) before changing code.
[Architecture](docs/ARCHITECTURE.md) · [Speech](docs/SPEECH_PIPELINE.md) ·
[Configuration](docs/CONFIGURATION.md) · [Security](docs/SECURITY.md) ·
[Release process](docs/RELEASES.md).

`VERSION` identifies the product release; its manifest records component versions,
source revision and checksums. Do not commit generated configuration, private
voice recordings, keys, models or build outputs.
