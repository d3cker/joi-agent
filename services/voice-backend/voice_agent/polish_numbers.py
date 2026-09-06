"""Polish language pack for locale-driven TTS number normalization.

Displayed assistant text is never changed. The pack turns structured numeric
expressions into unambiguous Polish prose before they reach multilingual TTS.
"""

from __future__ import annotations

import re

from .number_normalization import LocaleNumberSyntax, RegexRewrite, prepare_numeric_text


_ONES = (
    "zero", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem",
    "osiem", "dziewięć",
)
_TEENS = (
    "dziesięć", "jedenaście", "dwanaście", "trzynaście", "czternaście",
    "piętnaście", "szesnaście", "siedemnaście", "osiemnaście", "dziewiętnaście",
)
_TENS = (
    "", "dziesięć", "dwadzieścia", "trzydzieści", "czterdzieści",
    "pięćdziesiąt", "sześćdziesiąt", "siedemdziesiąt", "osiemdziesiąt",
    "dziewięćdziesiąt",
)
_HUNDREDS = (
    "", "sto", "dwieście", "trzysta", "czterysta", "pięćset", "sześćset",
    "siedemset", "osiemset", "dziewięćset",
)
_SCALES = (
    (1_000_000_000, ("miliard", "miliardy", "miliardów")),
    (1_000_000, ("milion", "miliony", "milionów")),
    (1_000, ("tysiąc", "tysiące", "tysięcy")),
)

# Linguistic inflection tables, not phrase-specific exceptions. The ordinal
# builder composes every supported value from the same case tables.
_ORDINAL_SMALL = {
    "nominative": (
        "zerowy", "pierwszy", "drugi", "trzeci", "czwarty", "piąty", "szósty",
        "siódmy", "ósmy", "dziewiąty", "dziesiąty", "jedenasty", "dwunasty",
        "trzynasty", "czternasty", "piętnasty", "szesnasty", "siedemnasty",
        "osiemnasty", "dziewiętnasty",
    ),
    "genitive": (
        "zerowego", "pierwszego", "drugiego", "trzeciego", "czwartego", "piątego",
        "szóstego", "siódmego", "ósmego", "dziewiątego", "dziesiątego",
        "jedenastego", "dwunastego", "trzynastego", "czternastego", "piętnastego",
        "szesnastego", "siedemnastego", "osiemnastego", "dziewiętnastego",
    ),
    "locative": (
        "zerowym", "pierwszym", "drugim", "trzecim", "czwartym", "piątym", "szóstym",
        "siódmym", "ósmym", "dziewiątym", "dziesiątym", "jedenastym", "dwunastym",
        "trzynastym", "czternastym", "piętnastym", "szesnastym", "siedemnastym",
        "osiemnastym", "dziewiętnastym",
    ),
}
_ORDINAL_TENS = {
    "nominative": (
        "", "", "dwudziesty", "trzydziesty", "czterdziesty", "pięćdziesiąty",
        "sześćdziesiąty", "siedemdziesiąty", "osiemdziesiąty", "dziewięćdziesiąty",
    ),
    "genitive": (
        "", "", "dwudziestego", "trzydziestego", "czterdziestego",
        "pięćdziesiątego", "sześćdziesiątego", "siedemdziesiątego",
        "osiemdziesiątego", "dziewięćdziesiątego",
    ),
    "locative": (
        "", "", "dwudziestym", "trzydziestym", "czterdziestym", "pięćdziesiątym",
        "sześćdziesiątym", "siedemdziesiątym", "osiemdziesiątym", "dziewięćdziesiątym",
    ),
}
_ORDINAL_HUNDREDS = {
    "nominative": (
        "", "setny", "dwusetny", "trzysetny", "czterechsetny", "pięćsetny",
        "sześćsetny", "siedemsetny", "osiemsetny", "dziewięćsetny",
    ),
    "genitive": (
        "", "setnego", "dwusetnego", "trzysetnego", "czterechsetnego", "pięćsetnego",
        "sześćsetnego", "siedemsetnego", "osiemsetnego", "dziewięćsetnego",
    ),
    "locative": (
        "", "setnym", "dwusetnym", "trzysetnym", "czterechsetnym", "pięćsetnym",
        "sześćsetnym", "siedemsetnym", "osiemsetnym", "dziewięćsetnym",
    ),
}
_ORDINAL_THOUSANDS = {
    "nominative": (
        "", "tysięczny", "dwutysięczny", "trzytysięczny", "czterotysięczny",
        "pięciotysięczny", "sześciotysięczny", "siedmiotysięczny", "ośmiotysięczny",
        "dziewięciotysięczny",
    ),
    "genitive": (
        "", "tysięcznego", "dwutysięcznego", "trzytysięcznego", "czterotysięcznego",
        "pięciotysięcznego", "sześciotysięcznego", "siedmiotysięcznego",
        "ośmiotysięcznego", "dziewięciotysięcznego",
    ),
    "locative": (
        "", "tysięcznym", "dwutysięcznym", "trzytysięcznym", "czterotysięcznym",
        "pięciotysięcznym", "sześciotysięcznym", "siedmiotysięcznym",
        "ośmiotysięcznym", "dziewięciotysięcznym",
    ),
}

_MONTHS = (
    "", "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca",
    "sierpnia", "września", "października", "listopada", "grudnia",
)
_MONTH_INDEX = {name: index for index, name in enumerate(_MONTHS) if name}
_MONTH_PATTERN = "|".join(re.escape(name) for name in _MONTH_INDEX)
_WEEKDAY_PATTERN = r"poniedziałek|wtorek|środa|czwartek|piątek|sobota|niedziela"

POLISH_NUMBER_SYNTAX = LocaleNumberSyntax(
    code="pl",
    grouping_separators=(" ", "\u00a0", "\u202f"),
    contextual_rewrites=(
        # Lower-case ``ok.`` before a number means ``około``. Upper-case ``OK``
        # remains an acknowledgement and is intentionally not rewritten.
        RegexRewrite(
            re.compile(
                r"(?<!\w)ok\.(?=[ \t\u00a0\u202f]+(?:,\s*)?[+-]?\d)"
            ),
            "około",
        ),
    ),
    sentence_abbreviations=("np", "tzn", "tj", "itp", "itd", "m.in", "nr", "dr", "prof", "ok"),
)

_DATE_DMY = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)")
_DATE_YMD = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_DATE_DM = re.compile(r"(?<!\d)(\d{2})[./](\d{2})(?![./]\d|\d)")
_DATE_WITH_MONTH_NAME = re.compile(
    rf"(?i)(?<!\d)(?P<day>\d{{1,2}})(?:-go)?\s+"
    rf"(?P<month>{_MONTH_PATTERN})"
    rf"(?:(?:,?\s+)(?P<year>\d{{4}})(?P<year_suffix>\s+(?:roku|r\.))?)?"
)
_YEAR_WITH_ROKU = re.compile(
    r"(?i)(?<!\d)(?P<year>\d{4})(?P<suffix>\s+(?:roku\b|r\.(?=\s|$)))"
)
_YEAR_AFTER_ROK = re.compile(r"(?i)(?P<prefix>\brok\s+)(?P<year>\d{4})(?!\d)")
_DAY_NUMBER_BEFORE_DNIA = re.compile(r"(?<!\d)(\d{1,2})\.(?=\s+dnia\b)", re.I)
_CURRENCY_PREFIX = re.compile(
    r"(?P<unit>[$€£])\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)"
)
_CURRENCY_SUFFIX = re.compile(
    r"(?i)(?<!\w)(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>zł|PLN|USD|EUR|GBP)(?!\w)"
)
_CURRENCY_FORMS = {
    "zł": ("złoty", "złote", "złotych"),
    "pln": ("złoty", "złote", "złotych"),
    "$": ("dolar", "dolary", "dolarów"),
    "usd": ("dolar", "dolary", "dolarów"),
    "€": ("euro", "euro", "euro"),
    "eur": ("euro", "euro", "euro"),
    "£": ("funt", "funty", "funtów"),
    "gbp": ("funt", "funty", "funtów"),
}
_DOTTED_SEQUENCE = re.compile(r"(?<!\w)\d+(?:\.\d+){2,}(?!\w)")
_DECIMAL = re.compile(r"(?<![\w.,])([+-]?\d+)([.,])(\d+)(?!\w|[.,]\d)")
_INTEGER = re.compile(r"(?<!\w)[+-]?\d+(?!\w)")
_PERCENT = re.compile(r"\s*%(?!\w)")

_NOMINATIVE_DATE_PREFIXES = (
    re.compile(
        rf"(?i)\b(?:dziś|dzisiaj)\s+jest\b[^.!?]*"
        rf"(?:{_WEEKDAY_PATTERN})\s*,\s*$"
    ),
    re.compile(r"(?i)\b(?:dziś|dzisiaj)\s+(?:jest|mamy)\s*$"),
    re.compile(r"(?i)\bmamy\s*$"),
    re.compile(r"(?i)\b(?:data|termin)\s*:\s*$"),
)


def _under_thousand(value: int) -> str:
    words: list[str] = []
    hundreds, remainder = divmod(value, 100)
    if hundreds:
        words.append(_HUNDREDS[hundreds])
    if 10 <= remainder <= 19:
        words.append(_TEENS[remainder - 10])
    else:
        tens, ones = divmod(remainder, 10)
        if tens:
            words.append(_TENS[tens])
        if ones:
            words.append(_ONES[ones])
    return " ".join(words)


def _scale_form(value: int, forms: tuple[str, str, str]) -> str:
    last_two = value % 100
    last = value % 10
    if value == 1:
        return forms[0]
    if 2 <= last <= 4 and not 12 <= last_two <= 14:
        return forms[1]
    return forms[2]


def integer_to_polish(value: int) -> str:
    """Return a cardinal Polish representation for an integer."""

    if value == 0:
        return _ONES[0]
    if value < 0:
        return "minus " + integer_to_polish(-value)
    if value >= 1_000_000_000_000:
        return " ".join(_ONES[int(digit)] for digit in str(value))

    words: list[str] = []
    remainder = value
    for scale, forms in _SCALES:
        count, remainder = divmod(remainder, scale)
        if not count:
            continue
        if count != 1:
            words.append(integer_to_polish(count))
        words.append(_scale_form(count, forms))
    if remainder:
        words.append(_under_thousand(remainder))
    return " ".join(words)


def ordinal_to_polish(value: int, grammatical_case: str = "nominative") -> str:
    """Compose a masculine ordinal in a supported grammatical case."""

    if grammatical_case not in _ORDINAL_SMALL:
        raise ValueError(f"unsupported Polish ordinal case: {grammatical_case}")
    if value < 0:
        return "minus " + ordinal_to_polish(-value, grammatical_case)
    if value < 20:
        return _ORDINAL_SMALL[grammatical_case][value]
    if value < 100:
        tens, ones = divmod(value, 10)
        result = _ORDINAL_TENS[grammatical_case][tens]
        if ones:
            result += " " + _ORDINAL_SMALL[grammatical_case][ones]
        return result
    if value < 1_000:
        hundreds, remainder = divmod(value, 100)
        if remainder == 0:
            return _ORDINAL_HUNDREDS[grammatical_case][hundreds]
        return f"{_HUNDREDS[hundreds]} {ordinal_to_polish(remainder, grammatical_case)}"
    if value < 10_000:
        thousands, remainder = divmod(value, 1_000)
        if remainder == 0:
            return _ORDINAL_THOUSANDS[grammatical_case][thousands]
        cardinal_prefix = integer_to_polish(thousands * 1_000)
        return f"{cardinal_prefix} {ordinal_to_polish(remainder, grammatical_case)}"
    # Preserving a grammatical cardinal is safer than spelling raw digits for
    # rare ordinal values above the supported calendar range.
    return integer_to_polish(value)


def year_to_polish(value: int, grammatical_case: str = "genitive") -> str:
    """Return the ordinal form conventionally used when reading a year."""

    return ordinal_to_polish(value, grammatical_case)


def _date_case(match: re.Match[str]) -> str:
    prefix = match.string[max(0, match.start() - 180) : match.start()]
    if any(pattern.search(prefix) for pattern in _NOMINATIVE_DATE_PREFIXES):
        return "nominative"
    return "genitive"


def _date(day: int, month: int, year: int, original: str, day_case: str) -> str:
    if not 1 <= day <= 31 or not 1 <= month <= 12:
        return original
    return (
        f"{ordinal_to_polish(day, day_case)} {_MONTHS[month]} "
        f"{year_to_polish(year, 'genitive')}"
    )


def _replace_named_date(match: re.Match[str]) -> str:
    day = int(match.group("day"))
    if not 1 <= day <= 31:
        return match.group(0)
    month = _MONTH_INDEX[match.group("month").casefold()]
    parts = [ordinal_to_polish(day, _date_case(match)), _MONTHS[month]]
    year = match.group("year")
    if year:
        parts.append(year_to_polish(int(year), "genitive"))
        if match.group("year_suffix"):
            parts.append("roku")
    return " ".join(parts)


def _replace_dmy(match: re.Match[str]) -> str:
    return _date(
        int(match.group(1)), int(match.group(2)), int(match.group(3)),
        match.group(0), _date_case(match),
    )


def _replace_ymd(match: re.Match[str]) -> str:
    return _date(
        int(match.group(3)), int(match.group(2)), int(match.group(1)),
        match.group(0), _date_case(match),
    )


def _replace_dm(match: re.Match[str]) -> str:
    day = int(match.group(1))
    month = int(match.group(2))
    if not 1 <= day <= 31 or not 1 <= month <= 12:
        return match.group(0)
    return f"{ordinal_to_polish(day, _date_case(match))} {_MONTHS[month]}"


def _replace_year_with_roku(match: re.Match[str]) -> str:
    prefix = match.string[max(0, match.start() - 20) : match.start()]
    grammatical_case = "locative" if re.search(r"(?i)\b(?:w|we)\s*$", prefix) else "genitive"
    return f"{year_to_polish(int(match.group('year')), grammatical_case)} roku"


def _replace_dotted_sequence(match: re.Match[str]) -> str:
    return " kropka ".join(
        integer_to_polish(int(component)) for component in match.group(0).split(".")
    )


def _replace_decimal(match: re.Match[str]) -> str:
    whole = integer_to_polish(int(match.group(1).replace("−", "-")))
    fraction = " ".join(_ONES[int(digit)] for digit in match.group(3))
    return f"{whole} przecinek {fraction}"


def _number_token_to_polish(value: str) -> str:
    normalized = value.replace("−", "-")
    separator = "," if "," in normalized else "." if "." in normalized else None
    if separator is None:
        return integer_to_polish(int(normalized))
    whole, fraction = normalized.split(separator, 1)
    return (
        f"{integer_to_polish(int(whole))} przecinek "
        + " ".join(_ONES[int(digit)] for digit in fraction)
    )


def _replace_currency(match: re.Match[str]) -> str:
    raw_value = match.group("value")
    forms = _CURRENCY_FORMS[match.group("unit").casefold()]
    normalized = raw_value.replace("−", "-").lstrip("+")
    if "," in normalized or "." in normalized:
        form = forms[2]
    else:
        form = _scale_form(abs(int(normalized)), forms)
    return f"{_number_token_to_polish(raw_value)} {form}"


def expand_polish_numbers(text: str) -> str:
    """Verbalize grouped values, dates, years, decimals and integers."""

    text = prepare_numeric_text(text, POLISH_NUMBER_SYNTAX)
    text = _DATE_YMD.sub(_replace_ymd, text)
    text = _DATE_DMY.sub(_replace_dmy, text)
    text = _DATE_DM.sub(_replace_dm, text)
    text = _DATE_WITH_MONTH_NAME.sub(_replace_named_date, text)
    text = _DAY_NUMBER_BEFORE_DNIA.sub(
        lambda match: ordinal_to_polish(int(match.group(1)), "genitive"), text
    )
    text = _YEAR_AFTER_ROK.sub(
        lambda match: match.group("prefix")
        + year_to_polish(int(match.group("year")), "nominative"),
        text,
    )
    text = _YEAR_WITH_ROKU.sub(_replace_year_with_roku, text)
    text = _CURRENCY_PREFIX.sub(_replace_currency, text)
    text = _CURRENCY_SUFFIX.sub(_replace_currency, text)
    text = _DOTTED_SEQUENCE.sub(_replace_dotted_sequence, text)
    text = _DECIMAL.sub(_replace_decimal, text)
    text = _INTEGER.sub(
        lambda match: integer_to_polish(int(match.group(0).replace("−", "-"))), text
    )
    return _PERCENT.sub(" procent", text)
