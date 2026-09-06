# Speech processing and diagnostics

## Input

AVAudioEngine captures the **system default input device**. Joi does not set
CurrentDevice on an unsupported VoiceProcessingIO Audio Unit. Device selection
is performed in macOS Sound settings, then refreshed in the app.

Each input channel is measured before conversion (RMS, peak, zero fraction).
The capture path selects a stable energetic channel rather than assuming channel
zero or summing opposite phases. A stateful resampler produces PCM16 mono
16 kHz; the exact 48-to-16 kHz path is deterministic and survives callback
boundaries. Float-to-Int16 conversion saturates. Never instantiate a resampler
for every callback or reuse old input buffers.

Voice processing reduces speaker echo. It is not proof against every room or
device. The microphone remains active while TTS plays, and stable VAD is
required before barge-in. All-zero callbacks produce a clear diagnostic warning.

## VAD and STT

The backend consumes the final PCM. Silero ONNX bundled with faster-whisper
runs in fixed windows and tracks prefix, speech, silence and suffix durations.
After natural silence it commits the utterance. Noise and silence do not start
STT. Whisper transcribes the selected conversation language with task=transcribe;
it must not translate the user's English into Polish.

Whisper acceptance considers no-speech probability, average log probability,
compression ratio and confirmed speech/energy. Do not blacklist individual
hallucinated sentences. CTranslate2 runs in a spawned process to isolate its
CUDA libraries from other runtimes.

## Output

Visible Markdown and session history remain unchanged. A separate speech
renderer turns Markdown into prose, reads table rows and trailing paragraphs,
removes control syntax/emoji, and normalizes structured values using the active
language pack. Parentheses must not switch language. URLs, code identifiers,
paths, dates, temperatures, abbreviations and grouped numbers require protected
recognition before punctuation splitting. See [number normalization](NUMBER_NORMALIZATION.md).

The incremental renderer buffers incomplete Markdown constructs. It emits
complete sentences while LLM text continues streaming. A single TTS worker
synthesizes ordered segments through the loopback Higgs HTTP service. Queue
depth and total TTS time do not contaminate LLM tokens/second.

The optional pre-generated Polish acknowledgement WAV is loaded once and can
play before full answer synthesis. English must not receive a Polish ack.
Higgs receives the reference audio path and exact transcript; use a
language-specific reference profile when needed. Raw model output is never
arbitrarily truncated to match a latency goal.

The Mac owns a persistent queue of full WAV segments, retaining players until
completion. Text completion does not dispose of playback. Response IDs and
atomic chunk markers prevent stale/cancelled audio from leaking into a new turn.

## Evidence required after audio changes

1. In the native signed app select system-default input and inspect actual
   device name, channel format and per-channel levels.
2. Speak during the five-second diagnostic recording. Listen to the saved WAV:
   it contains exactly the final PCM sent to the socket, not an alternate path.
3. Check nonzero callbacks, converted frames and sent bytes. All-zero input
   must not produce a misleading successful diagnostic file.
4. Run end-to-end with automatic commit, accepted transcript, a multi-segment
   answer and final playback acknowledgement.
5. Interrupt with real speech. Confirm cancellation and a complete next answer.
6. Check English and Polish, including Markdown tables, trailing sentences,
   grouped numbers, dates, ranges, paths and network addresses.

Tests using synthetic six-channel buffers cover channel selection/resampling.
They cannot prove physical microphone permission, routing, echo cancellation,
speaker audibility or subjective TTS quality. Report those separately.
