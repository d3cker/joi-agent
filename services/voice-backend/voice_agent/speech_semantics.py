"""Semantic text preparation shared by English and Polish speech rendering."""

from __future__ import annotations

import re

from .english_numbers import ENGLISH_NUMBER_SYNTAX, expand_english_numbers
from .number_normalization import NumberLanguagePack, NumberNormalizerRegistry
from .polish_numbers import POLISH_NUMBER_SYNTAX, expand_polish_numbers, integer_to_polish
from .structured_values import (
    IPV4_ENDPOINT,
    IPV6_INTERFACE,
    MEASUREMENT,
    NUMERIC_RANGE,
    verbalize_ipv4,
    verbalize_ipv6,
    verbalize_measurement,
    verbalize_numeric_range,
)


_URL = re.compile(r"(?i)\bhttps?://[^\s<>()\[\]{}]+")
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+")
_WINDOWS_PATH = re.compile(r"(?i)(?<!\w)[a-z]:\\[^\s,;!?()\[\]{}]+")
_POSIX_PATH = re.compile(r"(?<![\w:])(?:~|\.{1,2})?/[^\s,;!?()\[\]{}]+")
_DATE_DMY = re.compile(r"(?<!\d)\d{1,2}[./-]\d{1,2}[./-]\d{4}(?!\d)")
_DATE_YMD = re.compile(r"(?<!\d)\d{4}-\d{1,2}-\d{1,2}(?!\d)")
_VERSION = re.compile(r"(?i)(?<!\w)v?\d+(?:\.\d+){2,}(?!\w)")
_CLI_FLAG = re.compile(r"(?<!\w)--?[a-zA-Z][\w-]*")
_FILENAME = re.compile(
    r"(?i)(?<![\w.])[a-z0-9][\w-]*\.[a-z][a-z0-9]{0,7}(?![\w.])"
)
_SNAKE_OR_KEBAB = re.compile(
    r"(?<!\w)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)+(?!\w)"
)
_CAMEL = re.compile(r"(?<!\w)[a-z]+(?:[A-Z][A-Za-z0-9]*)+(?!\w)")
_DOMAIN = re.compile(r"(?i)(?<![\w@])(?:[a-z0-9-]+\.)+[a-z]{2,}(?!\w)")
_ASSIGNMENT = re.compile(r"(?<!\w)[A-Za-z_][\w.-]*=[^\s,;!?]+")
_PLAIN_LIST_LABEL = re.compile(r"(^|[.!?]\s+)(\d{1,3})[.,)](?=\s+\S)")
_ALPHA_DIGIT_BOUNDARY = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_SYMBOLS = {
    "en": {
        "/": "slash",
        "\\": "backslash",
        "_": "underscore",
        "-": "dash",
        ".": "dot",
        ":": "colon",
        "@": "at",
        "=": "equals",
        ">": "greater than",
        "<": "less than",
        "+": "plus",
        "#": "hash",
        "~": "tilde",
        "°": "degrees",
        "²": "squared",
        "³": "cubed",
    },
    "pl": {
        "/": "ukośnik",
        "\\": "ukośnik wsteczny",
        "_": "podkreślenie",
        "-": "myślnik",
        ".": "kropka",
        ":": "dwukropek",
        "@": "małpa",
        "=": "równa się",
        ">": "większe niż",
        "<": "mniejsze niż",
        "+": "plus",
        "#": "hash",
        "~": "tylda",
        "°": "stopni",
        "²": "kwadrat",
        "³": "sześcian",
    },
}

NUMBER_NORMALIZERS = NumberNormalizerRegistry({
    "en": NumberLanguagePack(ENGLISH_NUMBER_SYNTAX, expand_english_numbers),
    "pl": NumberLanguagePack(POLISH_NUMBER_SYNTAX, expand_polish_numbers),
})


def expand_numbers(text: str, language: str) -> str:
    return NUMBER_NORMALIZERS.normalize(text, language)


def sentence_abbreviations(language: str) -> tuple[str, ...]:
    return NUMBER_NORMALIZERS.syntax(language).sentence_abbreviations


def ordered_list_prefix(value: int, language: str) -> str:
    if language == "en":
        from .english_numbers import ordinal_to_english

        return ordinal_to_english(value).capitalize()
    polish = {
        1: "Po pierwsze",
        2: "Po drugie",
        3: "Po trzecie",
        4: "Po czwarte",
        5: "Po piąte",
        6: "Po szóste",
        7: "Po siódme",
        8: "Po ósme",
        9: "Po dziewiąte",
        10: "Po dziesiąte",
    }
    return polish.get(value, f"Punkt {integer_to_polish(value)}")


def verbalize_structured_text(text: str, language: str) -> str:
    """Replace structured spans with safe prose before number expansion.

    The replacements are protected while subsequent patterns run, preventing a
    path from being reinterpreted as an identifier or a URL as a POSIX path.
    """

    # Contextual numeric cues and Unicode signs must be resolved before the
    # plain-list heuristic. Otherwise ``(ok. 14)`` looks like sentence-ending
    # ``ok.`` followed by list item 14 and becomes "OK, point fourteen".
    text = NUMBER_NORMALIZERS.prepare(text, language)
    protected: list[str] = []

    # Some models emit spoken-looking lists such as ``1, first item`` rather
    # than canonical Markdown.  Treat those labels with the same ordinal
    # semantics as an ordered-list AST instead of leaving isolated digits for
    # the multilingual TTS model to language-guess.
    text = _PLAIN_LIST_LABEL.sub(
        lambda match: (
            f"{match.group(1)}{ordered_list_prefix(int(match.group(2)), language)},"
        ),
        text,
    )

    def marker(value: str) -> str:
        index = len(protected)
        letters = ""
        while True:
            index, remainder = divmod(index, 26)
            letters = chr(ord("A") + remainder) + letters
            if index == 0:
                break
            index -= 1
        protected.append(value)
        return f"\ue000{letters}\ue001"

    def replace(pattern: re.Pattern[str], converter) -> None:
        nonlocal text

        def callback(match: re.Match[str]) -> str:
            value = match.group(0)
            core, suffix = _strip_terminal_punctuation(value)
            spoken = converter(core)
            if spoken is None:
                return value
            return marker(expand_numbers(spoken, language)) + suffix

        text = pattern.sub(callback, text)

    replace(_URL, lambda value: _url_to_speech(value, language))
    replace(_EMAIL, lambda value: _email_to_speech(value, language))
    replace(_WINDOWS_PATH, lambda value: _path_to_speech(value, language, exact=False))
    replace(_POSIX_PATH, lambda value: _path_to_speech(value, language, exact=False))
    # Complete technical values outrank generic dotted versions and symbol
    # spelling. The converters validate addresses and use locale-owned words.
    replace(IPV4_ENDPOINT, lambda value: verbalize_ipv4(value, language))
    replace(IPV6_INTERFACE, lambda value: verbalize_ipv6(value, language))
    replace(NUMERIC_RANGE, lambda value: verbalize_numeric_range(value, language))
    replace(MEASUREMENT, lambda value: verbalize_measurement(value, language))
    # Numeric dates stay in the full sentence until the locale normalizer runs
    # below. That preserves grammatical context (for example nominative versus
    # genitive Polish dates) while the patterns above already protect URLs and
    # paths that could contain date-looking digits.
    replace(
        _VERSION,
        lambda value: (
            None
            if _DATE_DMY.fullmatch(value)
            else _version_to_speech(value, language)
        ),
    )
    replace(_CLI_FLAG, lambda value: _code_to_speech(value, language))
    replace(_ASSIGNMENT, lambda value: _code_to_speech(value, language))
    replace(_FILENAME, lambda value: _path_segment_to_speech(value, language, exact=False))
    replace(_SNAKE_OR_KEBAB, lambda value: _identifier_to_speech(value, language, exact=False))
    replace(_CAMEL, lambda value: _identifier_to_speech(value, language, exact=False))
    replace(_DOMAIN, lambda value: _domain_to_speech(value, language))

    text = expand_numbers(text, language)
    for index, value in enumerate(protected):
        letters = ""
        number = index
        while True:
            number, remainder = divmod(number, 26)
            letters = chr(ord("A") + remainder) + letters
            if number == 0:
                break
            number -= 1
        text = text.replace(f"\ue000{letters}\ue001", value)
    if language == "en":
        text = re.sub(r"(?i)\bversion\s+version\b", "version", text)
    else:
        text = re.sub(r"(?i)\bwersja\s+wersja\b", "wersja", text)
    return text


def verbalize_remaining_symbols(text: str, language: str) -> str:
    """Make unsafe technical separators audible when no richer span matched."""

    symbols = _SYMBOLS[language]
    # A remaining dash is prose punctuation, not a negative sign or range: the
    # structured numeric layer has already consumed those meanings. Converting
    # it to a pause prevents expressive models from treating it as a cue.
    text = text.replace("–", ", ").replace("—", ", ")
    text = text.replace("−", f" {symbols['-']} ")
    for character in ("_", "/", "\\", "=", ">", "<", "°", "²", "³"):
        text = text.replace(character, f" {symbols[character]} ")
    return re.sub(r"\s+", " ", text).strip()


def verbalize_inline_code(value: str, language: str) -> str:
    """Read inline code exactly enough to preserve separators and boundaries."""

    stripped = value.strip()
    if _WINDOWS_PATH.fullmatch(stripped) or _POSIX_PATH.fullmatch(stripped):
        return expand_numbers(_path_to_speech(stripped, language, exact=True), language)
    return expand_numbers(_code_to_speech(stripped, language), language)


def _strip_terminal_punctuation(value: str) -> tuple[str, str]:
    suffix = ""
    while value and value[-1] in ",;!?)]}":
        suffix = value[-1] + suffix
        value = value[:-1]
    # A final sentence period is not part of a path or URL.  Internal dots and
    # a real extension remain intact.
    if value.endswith("."):
        value = value[:-1]
        suffix = "." + suffix
    return value, suffix


def _split_identifier(value: str) -> list[str]:
    value = _CAMEL_BOUNDARY.sub(" ", value)
    value = _ALPHA_DIGIT_BOUNDARY.sub(" ", value)
    return [part for part in re.split(r"[_-]+|\s+", value) if part]


def _identifier_to_speech(value: str, language: str, *, exact: bool) -> str:
    if exact:
        return _code_to_speech(value, language)
    return " ".join(_split_identifier(value))


def _spell_extension(value: str) -> str:
    return " ".join(value) if value.isalpha() and len(value) <= 5 else value


def _path_segment_to_speech(segment: str, language: str, *, exact: bool) -> str:
    if exact:
        if "." in segment and not segment.startswith("."):
            stem, extension = segment.rsplit(".", 1)
            return (
                f"{_code_to_speech(stem, language)} {_SYMBOLS[language]['.']} "
                f"{_spell_extension(extension)}"
            ).strip()
        return _code_to_speech(segment, language)
    if "." in segment and not segment.startswith("."):
        stem, extension = segment.rsplit(".", 1)
        stem_words = " ".join(_split_identifier(stem))
        dot = _SYMBOLS[language]["."]
        return f"{stem_words} {dot} {_spell_extension(extension)}".strip()
    return " ".join(_split_identifier(segment))


def _path_to_speech(value: str, language: str, *, exact: bool) -> str:
    symbols = _SYMBOLS[language]
    if "\\" in value:
        drive, _, remainder = value.partition(":")
        segments = [part for part in remainder.split("\\") if part]
        prefix = f"{drive} {symbols[':']}"
        separator = f" {symbols['\\']} "
        body = separator.join(
            _path_segment_to_speech(part, language, exact=exact) for part in segments
        )
        return f"{prefix} {separator.strip()} {body}".strip()

    home = value.startswith("~/")
    rooted = value.startswith("/")
    relative_prefix = ""
    if value.startswith("../"):
        relative_prefix = "parent directory" if language == "en" else "katalog nadrzędny"
    elif value.startswith("./"):
        relative_prefix = "current directory" if language == "en" else "bieżący katalog"
    clean = value.removeprefix("~/").removeprefix("../").removeprefix("./").lstrip("/")
    segments = [part for part in clean.split("/") if part]
    spoken_segments = [
        _path_segment_to_speech(part, language, exact=exact) for part in segments
    ]
    if exact:
        separator = f" {symbols['/']} "
        body = separator.join(spoken_segments)
        if home:
            home_word = "home" if language == "en" else "katalog domowy"
            return f"{home_word} {symbols['/']} {body}".strip()
        if rooted:
            return f"{symbols['/']} {body}".strip()
        return " ".join(part for part in (relative_prefix, body) if part)
    prefix = "path" if language == "en" else "ścieżka"
    if home:
        prefix += " home" if language == "en" else " katalog domowy"
    elif relative_prefix:
        prefix += f" {relative_prefix}"
    return f"{prefix} " + ", ".join(spoken_segments)


def _domain_to_speech(value: str, language: str) -> str:
    dot = _SYMBOLS[language]["."]
    return f" {dot} ".join(value.split("."))


def _url_to_speech(value: str, language: str) -> str:
    without_scheme = re.sub(r"(?i)^https?://", "", value)
    host, separator, path = without_scheme.partition("/")
    spoken = _domain_to_speech(host, language)
    if separator and path:
        path_words = [
            _path_segment_to_speech(part, language, exact=False)
            for part in path.split("/")
            if part
        ]
        if path_words:
            spoken += ", " + ", ".join(path_words)
    return spoken


def _email_to_speech(value: str, language: str) -> str:
    local, domain = value.split("@", 1)
    local_words = " ".join(_split_identifier(local.replace(".", " ")))
    return f"{local_words} {_SYMBOLS[language]['@']} {_domain_to_speech(domain, language)}"


def _version_to_speech(value: str, language: str) -> str:
    explicit = value[:1].casefold() == "v"
    components = value.removeprefix("v").removeprefix("V").split(".")
    separator = "point" if language == "en" else "kropka"
    # Four bare numeric components are address-shaped. Valid IPv4 values were
    # already handled by the network classifier; invalid ones still must not
    # acquire the false semantic label "version".
    prefix = "version" if language == "en" else "wersja"
    if len(components) >= 4 and not explicit:
        prefix = ""
    spoken = f" {separator} ".join(components)
    return f"{prefix} {spoken}".strip()


def _code_to_speech(value: str, language: str) -> str:
    symbols = _SYMBOLS[language]
    parts: list[str] = []
    word = ""

    def flush() -> None:
        nonlocal word
        if word:
            parts.extend(_split_identifier(word))
            word = ""

    for character in value:
        if character.isalnum():
            word += character
        elif character.isspace():
            flush()
        else:
            flush()
            spoken = symbols.get(character)
            if spoken:
                parts.append(spoken)
    flush()
    return " ".join(parts)
