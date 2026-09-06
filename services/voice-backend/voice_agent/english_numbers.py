"""Dependency-free English number verbalization for the TTS frontend.

The visible assistant response remains unchanged.  The functions in this
module remove ambiguous digit tokens from the text sent to multilingual TTS,
so a short English segment cannot inherit Polish number pronunciation from a
voice prompt.
"""

from __future__ import annotations

import re

from .number_normalization import LocaleNumberSyntax, RegexRewrite, prepare_numeric_text


_ONES = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)
_ORDINAL_SMALL = {
    0: "zeroth",
    1: "first",
    2: "second",
    3: "third",
    4: "fourth",
    5: "fifth",
    6: "sixth",
    7: "seventh",
    8: "eighth",
    9: "ninth",
    10: "tenth",
    11: "eleventh",
    12: "twelfth",
    13: "thirteenth",
    14: "fourteenth",
    15: "fifteenth",
    16: "sixteenth",
    17: "seventeenth",
    18: "eighteenth",
    19: "nineteenth",
}
_ORDINAL_TENS = {
    20: "twentieth",
    30: "thirtieth",
    40: "fortieth",
    50: "fiftieth",
    60: "sixtieth",
    70: "seventieth",
    80: "eightieth",
    90: "ninetieth",
}
_MONTHS = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_MONTH_INDEX = {name.casefold(): index for index, name in enumerate(_MONTHS) if name}
_MONTH_PATTERN = "|".join(re.escape(name) for name in _MONTHS if name)

ENGLISH_NUMBER_SYNTAX = LocaleNumberSyntax(
    code="en",
    grouping_separators=(",", " ", "\u00a0", "\u202f"),
    contextual_rewrites=(
        RegexRewrite(
            re.compile(
                r"(?i)(?<!\w)(?:approx|ca)\."
                r"(?=[ \t\u00a0\u202f]+(?:,\s*)?[+-]?\d)"
            ),
            "approximately",
        ),
    ),
    sentence_abbreviations=("approx", "ca", "e.g", "i.e", "etc", "mr", "mrs", "ms", "dr", "prof", "no"),
)

_DATE_DMY = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)")
_DATE_YMD = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_DATE_MONTH_DAY_YEAR = re.compile(
    rf"(?i)(?<!\w)(?P<month>{_MONTH_PATTERN})\s+"
    rf"(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+)(?P<year>\d{{4}})(?!\d)"
)
_DATE_DAY_MONTH_YEAR = re.compile(
    rf"(?i)(?<!\d)(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+"
    rf"(?P<month>{_MONTH_PATTERN})(?:,?\s+)(?P<year>\d{{4}})(?!\d)"
)
_CONTEXTUAL_YEAR = re.compile(
    r"(?i)(?P<prefix>\b(?:year|in|since|from|during)\s+)(?P<year>\d{4})(?!\d)"
)
_CURRENCY_PREFIX = re.compile(
    r"(?P<unit>[$€£])\s*(?P<value>[+-]?\d+(?:\.\d+)?)"
)
_CURRENCY_SUFFIX = re.compile(
    r"(?i)(?<!\w)(?P<value>[+-]?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>USD|EUR|GBP)(?!\w)"
)
_CURRENCY_FORMS = {
    "$": ("dollar", "dollars"),
    "usd": ("dollar", "dollars"),
    "€": ("euro", "euros"),
    "eur": ("euro", "euros"),
    "£": ("pound", "pounds"),
    "gbp": ("pound", "pounds"),
}
_DOTTED_SEQUENCE = re.compile(r"(?<!\w)\d+(?:\.\d+){2,}(?!\w)")
_DECIMAL = re.compile(r"(?<![\w.,])([+-]?\d+)\.(\d+)(?!\w|[.,]\d)")
_INTEGER = re.compile(r"(?<!\w)[+-]?\d+(?!\w)")
_PERCENT = re.compile(r"\s*%(?!\w)")


def integer_to_english(value: int) -> str:
    """Return a cardinal English representation for an integer."""

    if value < 0:
        return "minus " + integer_to_english(-value)
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    if value < 1_000:
        hundreds, remainder = divmod(value, 100)
        result = f"{_ONES[hundreds]} hundred"
        return result + (f" {integer_to_english(remainder)}" if remainder else "")
    for scale, name in (
        (1_000_000_000_000, "trillion"),
        (1_000_000_000, "billion"),
        (1_000_000, "million"),
        (1_000, "thousand"),
    ):
        if value >= scale:
            count, remainder = divmod(value, scale)
            result = f"{integer_to_english(count)} {name}"
            return result + (f" {integer_to_english(remainder)}" if remainder else "")
    return " ".join(_ONES[int(digit)] for digit in str(value))


def year_to_english(value: int) -> str:
    """Use the conventional compact English reading for common years."""

    if 1000 <= value <= 1999:
        century, remainder = divmod(value, 100)
        if remainder == 0:
            return f"{integer_to_english(century)} hundred"
        if remainder < 10:
            return f"{integer_to_english(century)} oh {_ONES[remainder]}"
        return f"{integer_to_english(century)} {integer_to_english(remainder)}"
    if value == 2000:
        return "two thousand"
    if 2001 <= value <= 2009:
        return f"two thousand {_ONES[value - 2000]}"
    if 2010 <= value <= 2099:
        return f"twenty {integer_to_english(value - 2000)}"
    return integer_to_english(value)


def ordinal_to_english(value: int) -> str:
    """Return a natural ordinal used for Markdown and plain-text list labels."""

    if value in _ORDINAL_SMALL:
        return _ORDINAL_SMALL[value]
    if value in _ORDINAL_TENS:
        return _ORDINAL_TENS[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return f"{_TENS[tens]}-{_ORDINAL_SMALL[ones]}"
    if value < 1_000:
        hundreds, remainder = divmod(value, 100)
        if remainder == 0:
            return f"{_ONES[hundreds]} hundredth"
        return f"{_ONES[hundreds]} hundred {ordinal_to_english(remainder)}"
    return f"number {integer_to_english(value)}"


def _date(day: int, month: int, year: int, original: str) -> str:
    if not 1 <= day <= 31 or not 1 <= month <= 12:
        return original
    return f"{_MONTHS[month]} {ordinal_to_english(day)}, {year_to_english(year)}"


def _replace_dmy(match: re.Match[str]) -> str:
    return _date(
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        match.group(0),
    )


def _replace_ymd(match: re.Match[str]) -> str:
    return _date(
        int(match.group(3)),
        int(match.group(2)),
        int(match.group(1)),
        match.group(0),
    )


def _replace_named_date(match: re.Match[str]) -> str:
    day = int(match.group("day"))
    month = _MONTH_INDEX[match.group("month").casefold()]
    year = int(match.group("year"))
    return _date(day, month, year, match.group(0))


def _replace_dotted_sequence(match: re.Match[str]) -> str:
    return " point ".join(
        integer_to_english(int(component)) for component in match.group(0).split(".")
    )


def _replace_decimal(match: re.Match[str]) -> str:
    whole = integer_to_english(int(match.group(1).replace("−", "-")))
    fraction = " ".join(_ONES[int(digit)] for digit in match.group(2))
    return f"{whole} point {fraction}"


def _number_token_to_english(value: str) -> str:
    normalized = value.replace("−", "-")
    if "." not in normalized:
        return integer_to_english(int(normalized))
    whole, fraction = normalized.split(".", 1)
    return (
        f"{integer_to_english(int(whole))} point "
        + " ".join(_ONES[int(digit)] for digit in fraction)
    )


def _replace_currency(match: re.Match[str]) -> str:
    raw_value = match.group("value")
    singular, plural = _CURRENCY_FORMS[match.group("unit").casefold()]
    normalized = raw_value.replace("−", "-").lstrip("+")
    is_singular = re.fullmatch(r"-?0*1(?:\.0+)?", normalized) is not None
    return f"{_number_token_to_english(raw_value)} {singular if is_singular else plural}"


def expand_english_numbers(text: str) -> str:
    """Verbalize grouped values, dates, decimals and standalone integers."""

    text = prepare_numeric_text(text, ENGLISH_NUMBER_SYNTAX)
    text = _DATE_MONTH_DAY_YEAR.sub(_replace_named_date, text)
    text = _DATE_DAY_MONTH_YEAR.sub(_replace_named_date, text)
    text = _DATE_YMD.sub(_replace_ymd, text)
    text = _DATE_DMY.sub(_replace_dmy, text)
    text = _CONTEXTUAL_YEAR.sub(
        lambda match: match.group("prefix") + year_to_english(int(match.group("year"))),
        text,
    )
    text = _CURRENCY_PREFIX.sub(_replace_currency, text)
    text = _CURRENCY_SUFFIX.sub(_replace_currency, text)
    text = _DOTTED_SEQUENCE.sub(_replace_dotted_sequence, text)
    text = _DECIMAL.sub(_replace_decimal, text)

    def replace_integer(match: re.Match[str]) -> str:
        return integer_to_english(int(match.group(0).replace("−", "-")))

    text = _INTEGER.sub(replace_integer, text)
    return _PERCENT.sub(" percent", text)
