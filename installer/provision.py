"""Runs only inside the installation's backend venv, never the system Python."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from installer.common import ROOT, digest, write_json, atomic_write


def models(plan):
    from huggingface_hub import snapshot_download
    catalog = json.loads((ROOT / "installer/models.json").read_text())
    for kind, spec in catalog.items():
        target = Path(plan[f"{kind}_model"])
        if not plan.get(f"{kind}_model_supplied"):
            snapshot_download(spec["repository"], revision=spec["revision"], local_dir=target)
        for name in spec["files"]:
            if not (target / name).is_file() or (target / name).stat().st_size == 0:
                raise RuntimeError(f"Incomplete {kind} model: {target / name}")
        for name, expected in spec.get("sha256", {}).items():
            if digest(target / name) != expected:
                raise RuntimeError(f"Model checksum mismatch: {target / name}")
        write_json(Path(plan["release"]) / f"{kind}-model-receipt.json", {
            "path": str(target), "repository": spec["repository"],
            "revision": spec["revision"] if not plan.get(f"{kind}_model_supplied") else "user-supplied; SHA verified where specified",
            "verified_sha256": spec.get("sha256", {}),
        })


def bootstrap(plan):
    from voice_agent.config import Settings
    from voice_agent.config_store import ConfigStore
    settings = Settings(
        host="::" if ":" in plan["ip"] else "0.0.0.0", port=plan["port"], tls_enabled=True,
        tls_server_ip=plan["ip"],
        tls_certificate_path=str(Path(plan["config"]) / "security/server.crt"),
        tls_private_key_path=str(Path(plan["config"]) / "security/server.key"),
        llm_base_url=plan["llm_url"], llm_model=plan["llm_model"],
        stt_mode="real", stt_model_path=plan["stt_model"], vad_mode="real",
        tts_mode="real", tts_provider="openai_http", tts_voice_path=plan["voice"],
        tts_reference_text=Path(plan["transcript"]).read_text().strip(),
        tts_segment_soft_limit=180, tts_segment_hard_limit=260, tts_segment_max_words=45,
        ack_audio_path=str(Path(plan["data"]) / "ack.wav"),
        cuda_visible_devices=str(plan["gpu"]),
    )
    store = ConfigStore.bootstrap(settings, root=Path(plan["config"]),
        data_root=Path(plan["data"]), source_root=ROOT / "services/voice-backend")
    actual = store.load_settings()
    # Existing configuration is authoritative. Never silently overwrite UI edits.
    for field in ("port", "tls_enabled", "tls_server_ip", "cuda_visible_devices", "stt_model_path", "tts_voice_path"):
        if getattr(actual, field) != getattr(settings, field):
            raise RuntimeError(f"Existing configuration differs at {field}; preserve it or choose matching install arguments")


async def ack(plan):
    from voice_agent.config_store import ConfigStore
    from voice_agent.ack import ACK_TEXT, load_cached_acknowledgement
    from voice_agent.tts import OpenAICompatibleSpeechTTS
    settings = ConfigStore.discover().load_settings()
    if load_cached_acknowledgement(settings.ack_audio_path):
        return
    tts = OpenAICompatibleSpeechTTS(settings.tts_http_base_url, settings.tts_http_model,
        reference_audio_path=settings.tts_voice_path, reference_text=settings.tts_reference_text,
        temperature=settings.tts_temperature)
    try:
        result = await tts.synthesize(ACK_TEXT, language="pl")
        atomic_write(Path(settings.ack_audio_path), result.data)
        if not load_cached_acknowledgement(settings.ack_audio_path):
            raise RuntimeError("Generated acknowledgement is not a valid PCM WAV")
    finally:
        await tts.aclose()


async def probe(plan):
    """Real GPU STT/VAD check against cached synthesized speech, not a microphone claim."""
    import wave
    import numpy as np
    from voice_agent.config_store import ConfigStore
    from voice_agent.stt import FasterWhisperSTT, FasterWhisperVAD
    settings = ConfigStore.discover().load_settings()
    with wave.open(settings.ack_audio_path, "rb") as wav:
        rate = wav.getframerate()
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise RuntimeError("Readiness sample must be mono PCM16")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    # Offline fixture conversion only; the native client has its own stateful resampler.
    if rate != 16_000:
        samples = np.interp(np.arange(int(len(samples) * 16000 / rate)) * rate / 16000,
            np.arange(len(samples)), samples)
    pcm = np.clip(samples, -32768, 32767).astype("<i2").tobytes()
    vad = FasterWhisperVAD()
    if not await vad.contains_speech(pcm):
        raise RuntimeError("Generated acknowledgement did not pass real VAD")
    stt = FasterWhisperSTT(settings.stt_model_path, settings.stt_device,
        settings.stt_device_index, settings.stt_compute_type)
    try:
        result = await stt.transcribe(pcm, language="pl")
        if not result.accepted:
            raise RuntimeError("Generated acknowledgement did not pass real STT acceptance")
        write_json(Path(plan["release"]) / "speech-readiness.json", {
            "fixture": "cached Polish acknowledgement", "accepted": True,
            "transcript": result.text, "physical_microphone_test": False})
    finally:
        stt.close()


if __name__ == "__main__":
    action, plan_path = sys.argv[1:]
    plan = json.loads(Path(plan_path).read_text())
    if action == "models":
        models(plan)
    elif action == "bootstrap":
        bootstrap(plan)
    elif action == "ack":
        asyncio.run(ack(plan))
    elif action == "probe":
        asyncio.run(probe(plan))
    else:
        raise SystemExit("Unknown provisioning action")
