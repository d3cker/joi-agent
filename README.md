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

## Voice references

Higgs uses a short **reference recording** to reproduce a speaker's voice;
it is not a training dataset or a recording of your commands. The installer
requires two matching files:

- `--voice-reference`: clean speech from one consenting speaker, in WAV or FLAC.
- `--voice-transcript`: a UTF-8 text file containing exactly what that recording
  says, in its original language—not a system prompt or the agent's reply.
  The matching text helps the model reproduce the voice accurately.

Record your own voice, ask someone for a recording and permission, or use a
sample whose terms permit your intended use. Our development setup started
with the English female [en_f1.flac sample](https://storage.googleapis.com/chatterbox-demo-samples/mtl_prompts/en_f1.flac)
from the [official Chatterbox demo](https://github.com/resemble-ai/chatterbox/blob/master/multilingual_app.py).
We kept that reference when switching to Higgs, then used Higgs to generate a
short Polish recording in the same voice. Pairing that generated recording
with its Polish transcript provided a language-matched reference, helping
reduce unwanted English pronunciation in Polish responses. Separate language
references are optional; see [speech profiles](docs/CONFIGURATION.md#speech-profiles-and-migration).

The installer generates `ack.wav` (the quick Polish acknowledgement) itself;
you do not need to supply it. Reference recordings are not bundled with Joi.
Check a sample's usage and redistribution terms before reusing or sharing it.

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
