import asyncio
import io
import json
import wave

import httpx
import pytest

from voice_agent.tts import OpenAICompatibleSpeechTTS, VoiceReference, load_voice_reference_profiles


def _wav_bytes(*, sample_rate: int = 24_000, frames: int = 2_400) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x01" * frames)
    return output.getvalue()


def test_openai_compatible_tts_forwards_original_text_without_dictionary():
    received = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received.update(json.loads(request.content))
        return httpx.Response(200, content=_wav_bytes())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = OpenAICompatibleSpeechTTS(
        "http://tts.local/v1/",
        "bosonai/higgs-tts-3-4b",
        reference_audio_path="/voices/female.wav",
        reference_text="Reference transcript.",
        temperature=0.72,
        top_k=40,
        max_new_tokens=768,
        client=client,
    )

    result = asyncio.run(tts.synthesize("CoinMarketCap, np. dzisiaj."))
    asyncio.run(client.aclose())

    assert received == {
        "model": "bosonai/higgs-tts-3-4b",
        "input": "CoinMarketCap, np. dzisiaj.",
        "response_format": "wav",
        "stream": False,
        "temperature": 0.72,
        "top_k": 40,
        "max_new_tokens": 768,
        "references": [{
            "audio_path": "/voices/female.wav",
            "text": "Reference transcript.",
        }],
    }
    assert result.speak_text == "CoinMarketCap, np. dzisiaj."
    assert result.sample_rate == 24_000
    assert result.duration_seconds == 0.1


def test_voice_profiles_are_loaded_and_selected_by_conversation_language(tmp_path):
    profiles_path = tmp_path / "voice-profiles.json"
    profiles_path.write_text(
        json.dumps({
            "schema_version": 1,
            "profiles": {
                "pl": {"audio_path": "/voices/pl.wav", "text": "Polski tekst."},
                "en": {"audio_path": "/voices/en.wav", "text": "English text."},
            },
        }),
        encoding="utf-8",
    )
    profiles = load_voice_reference_profiles(str(profiles_path))
    assert profiles == {
        "pl": VoiceReference("/voices/pl.wav", "Polski tekst."),
        "en": VoiceReference("/voices/en.wav", "English text."),
    }

    received = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return httpx.Response(200, content=_wav_bytes())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = OpenAICompatibleSpeechTTS(
        "http://tts.local/v1",
        "higgs",
        reference_audio_path="/voices/default.wav",
        reference_text="Default text.",
        reference_profiles=profiles,
        client=client,
    )
    english = asyncio.run(tts.synthesize("Second, run it.", language="en"))
    polish = asyncio.run(tts.synthesize("Po drugie, uruchom.", language="pl"))
    asyncio.run(client.aclose())

    assert received[0]["references"] == [{
        "audio_path": "/voices/en.wav",
        "text": "English text.",
    }]
    assert received[1]["references"] == [{
        "audio_path": "/voices/pl.wav",
        "text": "Polski tekst.",
    }]
    assert english.voice_profile == "en"
    assert polish.voice_profile == "pl"


def test_missing_language_profile_falls_back_to_legacy_reference():
    received = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received.update(json.loads(request.content))
        return httpx.Response(200, content=_wav_bytes())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = OpenAICompatibleSpeechTTS(
        "http://tts.local/v1",
        "higgs",
        reference_audio_path="/voices/default.wav",
        reference_text="Default text.",
        reference_profiles={"pl": VoiceReference("/voices/pl.wav", "Polski tekst.")},
        client=client,
    )
    result = asyncio.run(tts.synthesize("English text.", language="en"))
    asyncio.run(client.aclose())

    assert received["references"] == [{
        "audio_path": "/voices/default.wav",
        "text": "Default text.",
    }]
    assert result.voice_profile == "default"


def test_voice_profile_document_rejects_unknown_languages(tmp_path):
    profiles_path = tmp_path / "voice-profiles.json"
    profiles_path.write_text(
        '{"schema_version":1,"profiles":{"xx":{"audio_path":"/voice.wav"}}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported conversation language"):
        load_voice_reference_profiles(str(profiles_path))


def test_openai_compatible_tts_rejects_non_wav_response():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"not a wave file")
        )
    )
    tts = OpenAICompatibleSpeechTTS(
        "http://tts.local/v1",
        "model",
        client=client,
    )

    with pytest.raises(RuntimeError, match="invalid WAV"):
        asyncio.run(tts.synthesize("Hello"))
    asyncio.run(client.aclose())
