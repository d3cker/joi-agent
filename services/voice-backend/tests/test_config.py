from pathlib import Path

import pytest

from voice_agent.config import Settings


def test_open_terminal_secret_can_be_loaded_from_runtime_file(monkeypatch, tmp_path: Path):
    secret_file = tmp_path / "open-terminal.key"
    secret_file.write_text("private-value\n", encoding="utf-8")
    monkeypatch.delenv("VOICE_AGENT_OPEN_TERMINAL_API_KEY", raising=False)
    monkeypatch.setenv("VOICE_AGENT_OPEN_TERMINAL_API_KEY_FILE", str(secret_file))
    assert Settings.from_env().open_terminal_api_key == "private-value"


def test_open_terminal_secret_rejects_ambiguous_sources(monkeypatch, tmp_path: Path):
    secret_file = tmp_path / "open-terminal.key"
    secret_file.write_text("file-value\n", encoding="utf-8")
    monkeypatch.setenv("VOICE_AGENT_OPEN_TERMINAL_API_KEY", "environment-value")
    monkeypatch.setenv("VOICE_AGENT_OPEN_TERMINAL_API_KEY_FILE", str(secret_file))
    with pytest.raises(ValueError, match="mutually exclusive"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "attribute"),
    [
        ("VOICE_AGENT_LLM_API_KEY", "llm_api_key"),
        ("VOICE_AGENT_TTS_HTTP_API_KEY", "tts_http_api_key"),
    ],
)
def test_model_secrets_support_private_runtime_files(
    monkeypatch, tmp_path: Path, name: str, attribute: str
):
    secret_file = tmp_path / f"{attribute}.key"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(f"{name}_FILE", str(secret_file))
    assert getattr(Settings.from_env(), attribute) == "file-secret"


def test_skill_catalog_configuration_from_environment(monkeypatch):
    monkeypatch.setenv("VOICE_AGENT_SKILLS_DIRECTORY", "/opt/voice-agent/skills")
    monkeypatch.setenv(
        "VOICE_AGENT_SKILLS_MANIFEST", "/opt/voice-agent/skills/manifest.json"
    )
    settings = Settings.from_env()
    assert settings.skills_directory == "/opt/voice-agent/skills"
    assert settings.skills_manifest_path == "/opt/voice-agent/skills/manifest.json"


def test_tts_voice_profiles_path_can_be_configured(monkeypatch):
    monkeypatch.setenv(
        "VOICE_AGENT_TTS_VOICE_PROFILES_PATH",
        "/home/user/.config/joi/backend/tts-voice-profiles.json",
    )

    assert Settings.from_env().tts_voice_profiles_path == (
        "/home/user/.config/joi/backend/tts-voice-profiles.json"
    )
