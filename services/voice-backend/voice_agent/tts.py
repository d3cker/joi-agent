from __future__ import annotations

from dataclasses import dataclass
import io
import json
import logging
from pathlib import Path
import wave
from typing import Protocol

import httpx

from .languages import conversation_language


log = logging.getLogger("uvicorn.error").getChild("voice_agent.tts")


@dataclass(frozen=True, slots=True)
class AudioResult:
    data: bytes
    mime_type: str = "audio/wav"
    sample_rate: int = 24_000
    duration_seconds: float | None = None
    trim_ms: int = 0
    speak_text: str | None = None
    voice_profile: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceReference:
    audio_path: str
    text: str | None = None


def load_voice_reference_profiles(path: str | None) -> dict[str, VoiceReference]:
    """Load language-specific Higgs prompts without downloading any assets."""

    if not path:
        return {}
    source = Path(path).expanduser()
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("tts_voice_profiles status=missing path=%s", source)
        return {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid TTS voice profile JSON: {exc.msg}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("TTS voice profile JSON requires schema_version 1")
    raw_profiles = document.get("profiles")
    if not isinstance(raw_profiles, dict):
        raise ValueError("TTS voice profile JSON requires a profiles object")
    profiles: dict[str, VoiceReference] = {}
    for raw_language, raw_profile in raw_profiles.items():
        language = conversation_language(str(raw_language)).code
        if not isinstance(raw_profile, dict):
            raise ValueError(f"TTS voice profile {language!r} must be an object")
        unknown = set(raw_profile) - {"audio_path", "text"}
        if unknown:
            raise ValueError(
                f"Unknown TTS voice profile fields for {language}: {', '.join(sorted(unknown))}"
            )
        audio_path = raw_profile.get("audio_path")
        reference_text = raw_profile.get("text")
        if not isinstance(audio_path, str) or not audio_path.strip():
            raise ValueError(f"TTS voice profile {language!r} requires audio_path")
        if reference_text is not None and (
            not isinstance(reference_text, str) or not reference_text.strip()
        ):
            raise ValueError(f"TTS voice profile {language!r} text must be non-empty")
        profiles[language] = VoiceReference(
            audio_path=audio_path,
            text=reference_text,
        )
    log.info(
        "tts_voice_profiles status=loaded path=%s languages=%s",
        source,
        ",".join(sorted(profiles)) or "none",
    )
    return profiles


class TTS(Protocol):
    async def synthesize(self, text: str, language: str = "pl") -> AudioResult: ...


class MockTTS:
    """Produces a short silent WAV, sufficient for end-to-end UI testing."""

    async def synthesize(self, text: str, language: str = "pl") -> AudioResult:
        conversation_language(language)
        sample_rate = 24_000
        frames = b"\0\0" * max(1, min(sample_rate // 4, len(text) * 120))
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(frames)
        return AudioResult(output.getvalue(), sample_rate=sample_rate)


class OpenAICompatibleSpeechTTS:
    """TTS adapter for a separate local OpenAI-compatible speech server.

    The preferred deployment target is Higgs TTS 3 served by SGLang-Omni or
    vLLM-Omni.  The voice-agent process stays independent from the model
    runtime and forwards the original text without a pronunciation dictionary.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        reference_audio_path: str | None = None,
        reference_text: str | None = None,
        reference_profiles: dict[str, VoiceReference] | None = None,
        temperature: float = 0.8,
        top_k: int = 50,
        max_new_tokens: int = 1024,
        timeout_seconds: float = 180.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.reference_audio_path = reference_audio_path
        self.reference_text = reference_text
        self.reference_profiles = dict(reference_profiles or {})
        self.temperature = temperature
        self.top_k = top_k
        self.max_new_tokens = max_new_tokens
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else None,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def synthesize(self, text: str, language: str = "pl") -> AudioResult:
        language = conversation_language(language).code
        payload: dict[str, object] = {
            "model": self.model,
            "input": text,
            "response_format": "wav",
            "stream": False,
            "temperature": self.temperature,
            "top_k": self.top_k,
            "max_new_tokens": self.max_new_tokens,
        }
        profile = self.reference_profiles.get(language)
        reference_audio_path = (
            profile.audio_path if profile is not None else self.reference_audio_path
        )
        reference_text = profile.text if profile is not None else self.reference_text
        voice_profile = language if profile is not None else "default"
        if reference_audio_path:
            reference: dict[str, str] = {"audio_path": reference_audio_path}
            if reference_text:
                reference["text"] = reference_text
            payload["references"] = [reference]

        response = await self._client.post(
            f"{self.base_url}/audio/speech",
            json=payload,
        )
        response.raise_for_status()
        data = response.content
        try:
            with wave.open(io.BytesIO(data), "rb") as wav:
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                sample_rate = wav.getframerate()
                frame_count = wav.getnframes()
        except (EOFError, wave.Error) as exc:
            raise RuntimeError("The TTS server returned an invalid WAV response") from exc
        if channels != 1 or sample_width != 2 or sample_rate <= 0 or frame_count <= 0:
            raise RuntimeError(
                "The TTS server must return non-empty PCM16 mono WAV audio; "
                f"received channels={channels}, sample_width={sample_width}, "
                f"sample_rate={sample_rate}, frames={frame_count}"
            )
        return AudioResult(
            data=data,
            sample_rate=sample_rate,
            duration_seconds=frame_count / sample_rate,
            speak_text=text,
            voice_profile=voice_profile,
        )
