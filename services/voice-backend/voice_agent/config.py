from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_SYSTEM_PROMPT_PATH = "prompts/default-system.md"


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _env_secret(name: str) -> str | None:
    value = os.getenv(name)
    file_path = os.getenv(f"{name}_FILE")
    if value and file_path:
        raise ValueError(f"{name} and {name}_FILE are mutually exclusive")
    if file_path:
        secret = Path(file_path).read_text(encoding="utf-8").strip()
        if not secret:
            raise ValueError(f"{name}_FILE is empty")
        return secret
    return value or None


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    tls_enabled: bool = False
    tls_server_ip: str = ""
    tls_certificate_path: str = ""
    tls_private_key_path: str = ""
    client_api_key: str | None = None
    llm_mode: str = "real"
    llm_base_url: str = "http://127.0.0.1:8888/v1"
    llm_model: str = "deepseek-v4-flash-0731"
    llm_context_size: int = 128_000
    llm_reasoning_levels: tuple[str, ...] = ("none", "low", "high", "max")
    llm_default_reasoning: str = "low"
    llm_api_key: str | None = None
    stt_mode: str = "mock"
    stt_model_path: str = "models/whisper-large-v3-turbo"
    stt_device: str = "cuda"
    stt_device_index: int = 0
    stt_compute_type: str = "float16"
    stt_no_speech_threshold: float = 0.60
    stt_log_probability_threshold: float = -1.0
    stt_compression_ratio_threshold: float = 2.4
    vad_mode: str = "mock"
    vad_start_threshold: float = 0.60
    vad_end_threshold: float = 0.35
    vad_min_speech_ms: int = 160
    vad_prefix_padding_ms: int = 320
    vad_silence_ms: int = 768
    vad_suffix_padding_ms: int = 160
    barge_in_threshold: float = 0.72
    barge_in_min_speech_ms: int = 256
    barge_in_prefix_ms: int = 96
    vad_min_rms_dbfs: float = -50.0
    tts_mode: str = "mock"
    tts_provider: str = "openai_http"
    tts_voice_path: str = "voices/reference.wav"
    tts_queue_max_segments: int = 32
    tts_segment_soft_limit: int = 78
    tts_segment_hard_limit: int = 84
    tts_segment_max_words: int = 12
    tts_defer_until_llm_done: bool = False
    tts_temperature: float = 0.6
    tts_http_base_url: str = "http://127.0.0.1:8790/v1"
    tts_http_model: str = "bosonai/higgs-tts-3-4b"
    tts_http_api_key: str | None = None
    tts_reference_text: str | None = None
    tts_voice_profiles_path: str | None = None
    tts_http_top_k: int = 50
    tts_http_max_new_tokens: int = 1024
    tts_http_timeout_seconds: float = 180.0
    ack_audio_path: str = "runtime/ack.wav"
    ack_min_characters: int = 60
    ack_min_words: int = 12
    cuda_visible_devices: str = "2"
    system_prompt: str | None = None
    system_prompt_path: str = DEFAULT_SYSTEM_PROMPT_PATH
    skills_directory: str = "skills"
    skills_manifest_path: str | None = None
    session_db_path: str = "runtime/sessions.sqlite3"
    agent_max_tool_steps: int = 8
    tool_mode: str = "off"
    open_terminal_base_url: str = "http://127.0.0.1:8000"
    open_terminal_api_key: str | None = None
    searxng_base_url: str = "http://127.0.0.1:8080"
    searxng_language: str = "pl-PL"
    web_fetch_user_agent: str = "LocalVoiceAgent/0.4"
    max_audio_seconds: int = 120

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=_env("VOICE_AGENT_HOST", "127.0.0.1"),
            port=int(_env("VOICE_AGENT_PORT", "8765")),
            tls_enabled=_env_bool("VOICE_AGENT_TLS_ENABLED", False),
            tls_server_ip=_env("VOICE_AGENT_TLS_SERVER_IP", ""),
            tls_certificate_path=_env("VOICE_AGENT_TLS_CERTIFICATE_PATH", ""),
            tls_private_key_path=_env("VOICE_AGENT_TLS_PRIVATE_KEY_PATH", ""),
            client_api_key=_env_secret("VOICE_AGENT_CLIENT_API_KEY"),
            llm_mode=_env("VOICE_AGENT_LLM_MODE", "real"),
            llm_base_url=_env("VOICE_AGENT_LLM_BASE_URL", "http://127.0.0.1:8888/v1").rstrip("/"),
            llm_model=_env("VOICE_AGENT_LLM_MODEL", "deepseek-v4-flash-0731"),
            llm_context_size=max(
                1, int(_env("VOICE_AGENT_LLM_CONTEXT_SIZE", "128000"))
            ),
            llm_reasoning_levels=tuple(
                item.strip()
                for item in _env(
                    "VOICE_AGENT_LLM_REASONING_LEVELS", "none,low,high,max"
                ).split(",")
                if item.strip()
            ),
            llm_default_reasoning=_env(
                "VOICE_AGENT_LLM_DEFAULT_REASONING", "low"
            ),
            llm_api_key=_env_secret("VOICE_AGENT_LLM_API_KEY"),
            stt_mode=_env("VOICE_AGENT_STT_MODE", "mock"),
            stt_model_path=_env("VOICE_AGENT_STT_MODEL_PATH", "models/whisper-large-v3-turbo"),
            stt_device=_env("VOICE_AGENT_STT_DEVICE", "cuda"),
            stt_device_index=int(_env("VOICE_AGENT_STT_DEVICE_INDEX", "0")),
            stt_compute_type=_env("VOICE_AGENT_STT_COMPUTE_TYPE", "float16"),
            stt_no_speech_threshold=float(_env("VOICE_AGENT_STT_NO_SPEECH_THRESHOLD", "0.60")),
            stt_log_probability_threshold=float(_env("VOICE_AGENT_STT_LOG_PROBABILITY_THRESHOLD", "-1.0")),
            stt_compression_ratio_threshold=float(_env("VOICE_AGENT_STT_COMPRESSION_RATIO_THRESHOLD", "2.4")),
            vad_mode=_env("VOICE_AGENT_VAD_MODE", "mock"),
            vad_start_threshold=float(_env("VOICE_AGENT_VAD_START_THRESHOLD", "0.60")),
            vad_end_threshold=float(_env("VOICE_AGENT_VAD_END_THRESHOLD", "0.35")),
            vad_min_speech_ms=int(_env("VOICE_AGENT_VAD_MIN_SPEECH_MS", "160")),
            vad_prefix_padding_ms=int(_env("VOICE_AGENT_VAD_PREFIX_PADDING_MS", "320")),
            vad_silence_ms=int(_env("VOICE_AGENT_VAD_SILENCE_MS", "768")),
            vad_suffix_padding_ms=int(_env("VOICE_AGENT_VAD_SUFFIX_PADDING_MS", "160")),
            barge_in_threshold=float(_env("VOICE_AGENT_BARGE_IN_THRESHOLD", "0.72")),
            barge_in_min_speech_ms=int(_env("VOICE_AGENT_BARGE_IN_MIN_SPEECH_MS", "256")),
            barge_in_prefix_ms=int(_env("VOICE_AGENT_BARGE_IN_PREFIX_MS", "96")),
            vad_min_rms_dbfs=float(_env("VOICE_AGENT_VAD_MIN_RMS_DBFS", "-50")),
            tts_mode=_env("VOICE_AGENT_TTS_MODE", "mock"),
            tts_provider=_env("VOICE_AGENT_TTS_PROVIDER", "openai_http"),
            tts_voice_path=_env("VOICE_AGENT_TTS_VOICE_PATH", "voices/reference.wav"),
            tts_queue_max_segments=max(
                1, int(_env("VOICE_AGENT_TTS_QUEUE_MAX_SEGMENTS", "32"))
            ),
            tts_segment_soft_limit=int(
                _env("VOICE_AGENT_TTS_SEGMENT_SOFT_LIMIT", "78")
            ),
            tts_segment_hard_limit=int(
                _env("VOICE_AGENT_TTS_SEGMENT_HARD_LIMIT", "84")
            ),
            tts_segment_max_words=int(
                _env("VOICE_AGENT_TTS_SEGMENT_MAX_WORDS", "12")
            ),
            tts_defer_until_llm_done=_env_bool(
                "VOICE_AGENT_TTS_DEFER_UNTIL_LLM_DONE", False
            ),
            tts_temperature=float(_env("VOICE_AGENT_TTS_TEMPERATURE", "0.60")),
            tts_http_base_url=_env(
                "VOICE_AGENT_TTS_HTTP_BASE_URL", "http://127.0.0.1:8790/v1"
            ).rstrip("/"),
            tts_http_model=_env(
                "VOICE_AGENT_TTS_HTTP_MODEL", "bosonai/higgs-tts-3-4b"
            ),
            tts_http_api_key=_env_secret("VOICE_AGENT_TTS_HTTP_API_KEY"),
            tts_reference_text=os.getenv("VOICE_AGENT_TTS_REFERENCE_TEXT"),
            tts_voice_profiles_path=os.getenv("VOICE_AGENT_TTS_VOICE_PROFILES_PATH"),
            tts_http_top_k=int(_env("VOICE_AGENT_TTS_HTTP_TOP_K", "50")),
            tts_http_max_new_tokens=int(
                _env("VOICE_AGENT_TTS_HTTP_MAX_NEW_TOKENS", "1024")
            ),
            tts_http_timeout_seconds=float(
                _env("VOICE_AGENT_TTS_HTTP_TIMEOUT_SECONDS", "180")
            ),
            ack_audio_path=_env(
                "VOICE_AGENT_ACK_AUDIO_PATH",
                "runtime/ack.wav",
            ),
            ack_min_characters=int(
                _env("VOICE_AGENT_ACK_MIN_CHARACTERS", "60")
            ),
            ack_min_words=int(_env("VOICE_AGENT_ACK_MIN_WORDS", "12")),
            cuda_visible_devices=_env("VOICE_AGENT_CUDA_VISIBLE_DEVICES", "2"),
            system_prompt=os.getenv("VOICE_AGENT_SYSTEM_PROMPT"),
            system_prompt_path=_env(
                "VOICE_AGENT_SYSTEM_PROMPT_PATH", DEFAULT_SYSTEM_PROMPT_PATH
            ),
            skills_directory=_env("VOICE_AGENT_SKILLS_DIRECTORY", "skills"),
            skills_manifest_path=os.getenv("VOICE_AGENT_SKILLS_MANIFEST"),
            session_db_path=_env(
                "VOICE_AGENT_SESSION_DB_PATH", "runtime/sessions.sqlite3"
            ),
            agent_max_tool_steps=max(
                1, int(_env("VOICE_AGENT_MAX_TOOL_STEPS", "8"))
            ),
            tool_mode=_env("VOICE_AGENT_TOOL_MODE", "off").lower(),
            open_terminal_base_url=_env(
                "VOICE_AGENT_OPEN_TERMINAL_BASE_URL", "http://127.0.0.1:8000"
            ).rstrip("/"),
            open_terminal_api_key=_env_secret("VOICE_AGENT_OPEN_TERMINAL_API_KEY"),
            searxng_base_url=_env(
                "VOICE_AGENT_SEARXNG_BASE_URL", "http://127.0.0.1:8080"
            ).rstrip("/"),
            searxng_language=_env("VOICE_AGENT_SEARXNG_LANGUAGE", "pl-PL"),
            web_fetch_user_agent=_env(
                "VOICE_AGENT_WEB_FETCH_USER_AGENT", "LocalVoiceAgent/0.4"
            ),
            max_audio_seconds=int(_env("VOICE_AGENT_MAX_AUDIO_SECONDS", "120")),
        )


def validate_settings(settings: Settings) -> None:
    """Validate configuration without loading models or opening the network."""
    if not settings.host.strip():
        raise ValueError("host must not be empty")
    if not 1 <= settings.port <= 65_535:
        raise ValueError("port must be between 1 and 65535")
    if settings.tls_enabled:
        if not settings.tls_server_ip.strip():
            raise ValueError("security.server_ip must not be empty when TLS is enabled")
        if not settings.tls_certificate_path.strip():
            raise ValueError("security.certificate_path must not be empty when TLS is enabled")
        if not settings.tls_private_key_path.strip():
            raise ValueError("security.private_key_path must not be empty when TLS is enabled")
    if settings.llm_mode not in {"mock", "real"}:
        raise ValueError("llm.mode must be 'mock' or 'real'")
    _require_http_url("llm.base_url", settings.llm_base_url)
    if not settings.llm_model.strip():
        raise ValueError("llm.model must not be empty")
    if settings.llm_context_size < 1:
        raise ValueError("llm.context_size must be positive")
    if not settings.llm_reasoning_levels:
        raise ValueError("llm.reasoning_levels must not be empty")
    if len(settings.llm_reasoning_levels) != len(set(settings.llm_reasoning_levels)):
        raise ValueError("llm.reasoning_levels must be unique")
    if settings.llm_default_reasoning not in settings.llm_reasoning_levels:
        raise ValueError("llm.default_reasoning must be present in reasoning_levels")
    if settings.stt_mode not in {"mock", "real"}:
        raise ValueError("stt.mode must be 'mock' or 'real'")
    if settings.vad_mode not in {"mock", "real"}:
        raise ValueError("vad.mode must be 'mock' or 'real'")
    if settings.tts_mode not in {"mock", "real"}:
        raise ValueError("tts.mode must be 'mock' or 'real'")
    if settings.tts_provider not in {"openai_http"}:
        raise ValueError("tts.provider must be 'openai_http'")
    if settings.tts_provider == "openai_http":
        _require_http_url("tts.http_base_url", settings.tts_http_base_url)
    if settings.tool_mode not in {"off", "yolo"}:
        raise ValueError("tools.mode must be 'off' or 'yolo'")
    _require_http_url("tools.open_terminal_base_url", settings.open_terminal_base_url)
    _require_http_url("tools.searxng_base_url", settings.searxng_base_url)
    if not settings.cuda_visible_devices.isdecimal():
        raise ValueError("runtime.cuda_visible_devices must select one physical GPU index")
    if settings.max_audio_seconds < 1:
        raise ValueError("server.max_audio_seconds must be positive")
    if settings.agent_max_tool_steps < 1:
        raise ValueError("agent.max_tool_steps must be positive")
    if not 0 < settings.tts_temperature <= 2:
        raise ValueError("tts.temperature must be greater than 0 and at most 2")
    for name in (
        "tts_queue_max_segments",
        "tts_segment_soft_limit",
        "tts_segment_hard_limit",
        "tts_segment_max_words",
        "tts_http_top_k",
        "tts_http_max_new_tokens",
    ):
        if getattr(settings, name) < 1:
            raise ValueError(f"tts.{name.removeprefix('tts_')} must be positive")
    if settings.tts_segment_soft_limit > settings.tts_segment_hard_limit:
        raise ValueError("tts.segment_soft_limit must not exceed segment_hard_limit")


def _require_http_url(name: str, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be an http:// or https:// URL")
