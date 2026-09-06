# HTTP and WebSocket contract

The native client connects to `wss://<server-ip>:8765/ws`.
HTTPS uses the same listener. Cleartext is for loopback tests only.
Authentication is described in [security](SECURITY.md).

A durable session is selected by `session_id` in the WebSocket query; it is
an identifier, not a credential. `conversation_language=en|pl` selects the
conversation locale. The protocol parser and Swift event decoder are the
authoritative field definitions. Add optional telemetry compatibly; do not
silently rename event fields.

## Client to backend

| Message | Meaning |
| --- | --- |
| binary | Signed little-endian PCM16, mono, 16 kHz; no WAV header |
| session.configure | Explicit reasoning_effort and optional conversation_language |
| input_audio.commit | Emergency manual commit, subject to the speech gate |
| response.cancel | Cancel current response, queued synthesis and audio |
| output_audio.playback.done | Client drained a response; requires response_id |
| ping | Keepalive; server answers pong |

The legacy spelling output_audio.playback_done is accepted. Binary input must
contain an even number of bytes. Normal turn-taking never needs manual commit.
Silero confirms stable speech and commits after configured silence. Manual
commit with no confirmed speech produces a user-facing error, not Whisper
hallucinations.

Current protocol reasoning levels are none, low, high and max. Configuration
can list model choices, but arbitrary new wire values also require parser/client
support: do not claim an unrestricted enum merely by editing a model profile.

## Backend to client

| Family | Purpose |
| --- | --- |
| session.* | Configuration and current processing state |
| input_audio.* | VAD/capture diagnostics and commit information |
| transcript.final | Accepted transcript and STT metrics |
| assistant.delta | Immediate visible content delta, never hidden reasoning |
| assistant.done | Text is complete; audio may still be arriving |
| audio.start | One start marker per response |
| audio.chunk + binary WAV | One indexed, complete audio segment |
| audio.end | Server finished or cancelled audio production |
| tool.* | Lifecycle, arguments, call ID and result/error metadata |
| metrics / diagnostic events | Latency, LLM throughput, queue/capture state |
| error | Machine-readable code and user-facing message |

See event calls in voice_agent/session.py for exact event names and payloads.
A cached acknowledgement is audio index 0 with cached_ack=true. It must
include duration and transcript-to-ack timing but has no synthesis latency.

## Ordering and cancellation invariants

1. Every generated answer has its own response_id.
2. A marker and its binary WAV are emitted under the same send lock; a
   JSON message cannot interleave between them.
3. Segment indices increase monotonically, including cached ack.
4. LLM SSE consumption and assistant.delta never wait for TTS inference.
5. assistant.done can precede remaining audio. It must not stop playback.
6. audio.end means the server has finished sending, not that speakers drained.
7. The client acknowledges local drain with output_audio.playback.done.
8. Barge-in cancels the response's worker and queued segments; stale audio is
   ignored. Unrelated OpenTerminal tasks are not automatically killed.

## HTTP management

Client-key protected routes:

- GET/POST /sessions; GET/PATCH/DELETE /sessions/{id}.
- GET /tools and GET /skills.
- Session details contain public messages and tool lifecycle metadata.
  Raw successful tool output remains in private model context.

Separate admin-key protected routes:

- GET /config and /config/schema.
- PATCH /config with document changes and expected revision.
- PUT /config/secrets/{name}; secret writes are not echoed.
- POST /config/rollback, /config/reload, /config/restart.
- POST /config/test/{target} for bounded connectivity/component checks.

GET /health is public. Configuration save persists files; component reload or
supervised process restart is explicit. A restart response must be delivered
before the process exits. See app.py for status codes and body validation.

## Metrics

TTFT is measured against the LLM request, not completion of synthesis. LLM
generation time ends with SSE; tokens/second uses API completion usage where
available and labels fallback text estimates. TTS total latency and queue depth
are separate. Capture reports input callbacks, converted frames, sent frames,
sent bytes and per-channel levels. Metrics must not rewrite conversation text.
