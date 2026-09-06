from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConversationLanguage:
    code: str
    display_name: str
    stt_language: str


_LANGUAGES = {
    "en": ConversationLanguage("en", "English", "en"),
    "pl": ConversationLanguage("pl", "Polish", "pl"),
}

DEFAULT_CONVERSATION_LANGUAGE = "pl"


def conversation_language(value: str) -> ConversationLanguage:
    code = value.strip().lower().replace("_", "-").split("-", 1)[0]
    try:
        return _LANGUAGES[code]
    except KeyError as exc:
        supported = ", ".join(sorted(_LANGUAGES))
        raise ValueError(f"unsupported conversation language {value!r}; supported: {supported}") from exc


def language_directive(code: str) -> str:
    language = conversation_language(code)
    return (
        f"The active conversation language is {language.display_name} ({language.code}). "
        f"Preserve the user's words in {language.display_name}; do not translate their "
        "transcript into another language. Respond entirely in "
        f"{language.display_name}, including prose, headings, labels, tool-result summaries, "
        "and user-facing status messages, unless the user explicitly requests another language."
    )


def localized_message(code: str, key: str) -> str:
    messages = {
        "en": {
            "no_speech": "No speech detected",
            "empty_audio": "No audio to send",
        },
        "pl": {
            "no_speech": "Nie wykryto mowy",
            "empty_audio": "Brak dźwięku do wysłania",
        },
    }
    return messages[conversation_language(code).code][key]
