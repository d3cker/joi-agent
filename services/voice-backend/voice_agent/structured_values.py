"""Locale-configurable speech for measurements and network addresses.

This layer recognizes complete technical values before generic dotted-number,
version, path, and symbol rules run. The recognition engine is language
neutral; adding a locale means registering words and plural rules in one pack.
Displayed Markdown is never modified.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import ipaddress
import re
from typing import Callable, Mapping

from .unit_catalog import SPACED_ONLY_ALIASES, TERMINAL_ONLY_ALIASES, UNIT_CATALOG


PluralRule = Callable[[Decimal], str]


@dataclass(frozen=True, slots=True)
class UnitLexeme:
    key: str
    aliases: tuple[str, ...]
    forms: Mapping[str, str]
    gender: str = "m"


@dataclass(frozen=True, slots=True)
class StructuredSpeechPack:
    code: str
    address: str
    ipv6_address: str
    prefix: str
    port: str
    dot: str
    colon: str
    double_colon: str
    minimum: str
    maximum: str
    plural_rule: PluralRule
    units: tuple[UnitLexeme, ...]
    quantity_rule: Callable[[str, UnitLexeme], str] = lambda raw, unit: raw


def _polish_plural(value: Decimal) -> str:
    absolute = abs(value)
    if absolute != absolute.to_integral_value():
        return "fraction"
    integer = int(absolute)
    if integer == 1:
        return "one"
    if integer % 10 in (2, 3, 4) and integer % 100 not in (12, 13, 14):
        return "few"
    return "many"


def _english_plural(value: Decimal) -> str:
    return "one" if abs(value) == 1 else "many"


def _forms(one: str, many: str, few: str | None = None) -> Mapping[str, str]:
    return {"one": one, "few": few or many, "many": many}


_PL_UNITS = (
    UnitLexeme("celsius", ("°C", "°c", "° C", "° c", "℃"), _forms("stopień Celsjusza", "stopni Celsjusza", "stopnie Celsjusza")),
    UnitLexeme("fahrenheit", ("°F", "°f", "° F", "° f", "℉"), _forms("stopień Fahrenheita", "stopni Fahrenheita", "stopnie Fahrenheita")),
    UnitLexeme("kilometers_per_hour", ("km/h",), _forms("kilometr na godzinę", "kilometrów na godzinę", "kilometry na godzinę")),
    UnitLexeme("meters_per_second", ("m/s",), _forms("metr na sekundę", "metrów na sekundę", "metry na sekundę")),
    UnitLexeme("square_kilometers", ("km²",), _forms("kilometr kwadratowy", "kilometrów kwadratowych", "kilometry kwadratowe")),
    UnitLexeme("square_meters", ("m²",), _forms("metr kwadratowy", "metrów kwadratowych", "metry kwadratowe")),
    UnitLexeme("square_centimeters", ("cm²",), _forms("centymetr kwadratowy", "centymetrów kwadratowych", "centymetry kwadratowe")),
    UnitLexeme("cubic_meters", ("m³",), _forms("metr sześcienny", "metrów sześciennych", "metry sześcienne")),
    UnitLexeme("gigabits_per_second", ("Gb/s", "Gbit/s", "Gbps"), _forms("gigabit na sekundę", "gigabitów na sekundę", "gigabity na sekundę")),
    UnitLexeme("megabits_per_second", ("Mb/s", "Mbit/s", "Mbps"), _forms("megabit na sekundę", "megabitów na sekundę", "megabity na sekundę")),
    UnitLexeme("kilobits_per_second", ("kb/s", "kbit/s", "kbps"), _forms("kilobit na sekundę", "kilobitów na sekundę", "kilobity na sekundę")),
    UnitLexeme("kilowatt_hours", ("kWh",), _forms("kilowatogodzina", "kilowatogodzin", "kilowatogodziny")),
    UnitLexeme("gigahertz", ("GHz",), _forms("gigaherc", "gigaherców")),
    UnitLexeme("megahertz", ("MHz",), _forms("megaherc", "megaherców")),
    UnitLexeme("kilohertz", ("kHz",), _forms("kiloherc", "kiloherców")),
    UnitLexeme("hertz", ("Hz",), _forms("herc", "herców")),
    UnitLexeme("terabytes", ("TB", "TiB"), _forms("terabajt", "terabajtów", "terabajty")),
    UnitLexeme("gigabytes", ("GB", "GiB"), _forms("gigabajt", "gigabajtów", "gigabajty")),
    UnitLexeme("megabytes", ("MB", "MiB"), _forms("megabajt", "megabajtów", "megabajty")),
    UnitLexeme("kilobytes", ("KB", "KiB"), _forms("kilobajt", "kilobajtów", "kilobajty")),
    UnitLexeme("kilowatts", ("kW",), _forms("kilowat", "kilowatów", "kilowaty")),
    UnitLexeme("watts", ("W",), _forms("wat", "watów", "waty")),
    UnitLexeme("volts", ("V",), _forms("wolt", "woltów", "wolty")),
    UnitLexeme("amperes", ("A",), _forms("amper", "amperów", "ampery")),
    UnitLexeme("kilograms", ("kg",), _forms("kilogram", "kilogramów", "kilogramy")),
    UnitLexeme("grams", ("g",), _forms("gram", "gramów", "gramy")),
    UnitLexeme("kilometers", ("km",), _forms("kilometr", "kilometrów", "kilometry")),
    UnitLexeme("centimeters", ("cm",), _forms("centymetr", "centymetrów", "centymetry")),
    UnitLexeme("millimeters", ("mm",), _forms("milimetr", "milimetrów", "milimetry")),
    UnitLexeme("meters", ("m",), _forms("metr", "metrów", "metry")),
    UnitLexeme("milliseconds", ("ms",), _forms("milisekunda", "milisekund", "milisekundy")),
    UnitLexeme("seconds", ("s",), _forms("sekunda", "sekund", "sekundy")),
    UnitLexeme("hours", ("h",), _forms("godzina", "godzin", "godziny")),
)

_EN_UNITS = (
    UnitLexeme("celsius", ("°C", "°c", "° C", "° c", "℃"), _forms("degree Celsius", "degrees Celsius")),
    UnitLexeme("fahrenheit", ("°F", "°f", "° F", "° f", "℉"), _forms("degree Fahrenheit", "degrees Fahrenheit")),
    UnitLexeme("kilometers_per_hour", ("km/h",), _forms("kilometer per hour", "kilometers per hour")),
    UnitLexeme("meters_per_second", ("m/s",), _forms("meter per second", "meters per second")),
    UnitLexeme("square_kilometers", ("km²",), _forms("square kilometer", "square kilometers")),
    UnitLexeme("square_meters", ("m²",), _forms("square meter", "square meters")),
    UnitLexeme("square_centimeters", ("cm²",), _forms("square centimeter", "square centimeters")),
    UnitLexeme("cubic_meters", ("m³",), _forms("cubic meter", "cubic meters")),
    UnitLexeme("gigabits_per_second", ("Gb/s", "Gbit/s", "Gbps"), _forms("gigabit per second", "gigabits per second")),
    UnitLexeme("megabits_per_second", ("Mb/s", "Mbit/s", "Mbps"), _forms("megabit per second", "megabits per second")),
    UnitLexeme("kilobits_per_second", ("kb/s", "kbit/s", "kbps"), _forms("kilobit per second", "kilobits per second")),
    UnitLexeme("kilowatt_hours", ("kWh",), _forms("kilowatt-hour", "kilowatt-hours")),
    UnitLexeme("gigahertz", ("GHz",), _forms("gigahertz", "gigahertz")),
    UnitLexeme("megahertz", ("MHz",), _forms("megahertz", "megahertz")),
    UnitLexeme("kilohertz", ("kHz",), _forms("kilohertz", "kilohertz")),
    UnitLexeme("hertz", ("Hz",), _forms("hertz", "hertz")),
    UnitLexeme("terabytes", ("TB", "TiB"), _forms("terabyte", "terabytes")),
    UnitLexeme("gigabytes", ("GB", "GiB"), _forms("gigabyte", "gigabytes")),
    UnitLexeme("megabytes", ("MB", "MiB"), _forms("megabyte", "megabytes")),
    UnitLexeme("kilobytes", ("KB", "KiB"), _forms("kilobyte", "kilobytes")),
    UnitLexeme("kilowatts", ("kW",), _forms("kilowatt", "kilowatts")),
    UnitLexeme("watts", ("W",), _forms("watt", "watts")),
    UnitLexeme("volts", ("V",), _forms("volt", "volts")),
    UnitLexeme("amperes", ("A",), _forms("ampere", "amperes")),
    UnitLexeme("kilograms", ("kg",), _forms("kilogram", "kilograms")),
    UnitLexeme("grams", ("g",), _forms("gram", "grams")),
    UnitLexeme("kilometers", ("km",), _forms("kilometer", "kilometers")),
    UnitLexeme("centimeters", ("cm",), _forms("centimeter", "centimeters")),
    UnitLexeme("millimeters", ("mm",), _forms("millimeter", "millimeters")),
    UnitLexeme("meters", ("m",), _forms("meter", "meters")),
    UnitLexeme("milliseconds", ("ms",), _forms("millisecond", "milliseconds")),
    UnitLexeme("seconds", ("s",), _forms("second", "seconds")),
    UnitLexeme("hours", ("h",), _forms("hour", "hours")),
)


def _polish_quantity(raw: str, unit: UnitLexeme) -> str:
    """Agree whole feminine quantities without changing decimal semantics."""
    value = Decimal(raw.replace(",", "."))
    if unit.gender != "f" or value != value.to_integral_value():
        return raw
    from .polish_numbers import integer_to_polish

    number = int(value)
    words = integer_to_polish(number)
    if abs(number) == 1:
        words = re.sub(r"jeden$", "jedna", words)
    elif abs(number) % 10 == 2 and abs(number) % 100 != 12:
        words = re.sub(r"dwa$", "dwie", words)
    return words


def _catalog_units(language: str) -> tuple[UnitLexeme, ...]:
    entries = []
    for aliases, polish, english, gender in UNIT_CATALOG:
        symbols = tuple(aliases.split("|"))
        one, few, many, fraction = polish.split("|")
        en_one, en_many = english.split("|")
        forms = ({"one": one, "few": few, "many": many, "fraction": fraction}
                 if language == "pl" else _forms(en_one, en_many))
        entries.append(UnitLexeme("catalog:" + symbols[0], symbols, forms, gender))
    return tuple(entries)


STRUCTURED_SPEECH_PACKS = {
    "pl": StructuredSpeechPack(
        "pl", "adres", "adres IP sześć", "prefiks", "port", "kropka",
        "dwukropek", "podwójny dwukropek", "minimum", "maksimum",
        _polish_plural, _PL_UNITS + _catalog_units("pl"), _polish_quantity,
    ),
    "en": StructuredSpeechPack(
        "en", "address", "IPv six address", "prefix", "port", "dot",
        "colon", "double colon", "minimum", "maximum",
        _english_plural, _EN_UNITS + _catalog_units("en"),
    ),
}


def _all_aliases() -> tuple[str, ...]:
    return tuple(sorted(
        {alias for pack in STRUCTURED_SPEECH_PACKS.values() for unit in pack.units for alias in unit.aliases},
        key=len,
        reverse=True,
    ))


_NUMBER = r"[+-]?\d+(?:[.,]\d+)?"


def _alias_pattern() -> str:
    # Do not guess units from ordinary words ("2 a potem", "5 in stock").
    # A terminal-only alias can still precede a sentence end or table delimiter.
    terminal = r"(?=\s*(?:$|[.,;:!?\)\]\|]))"
    return "|".join(
        (r"(?<=\s)" if alias in SPACED_ONLY_ALIASES else "")
        + re.escape(alias) + (terminal if alias in TERMINAL_ONLY_ALIASES else "")
        for alias in _all_aliases()
    )


MEASUREMENT = re.compile(
    rf"(?<![\w.])(?P<value>{_NUMBER})\s*(?P<unit>"
    + _alias_pattern()
    + r")(?![\w/²³^])"
)
NUMERIC_RANGE = re.compile(
    rf"(?<![\w.])(?P<start>{_NUMBER})\s*[\u2013\u2014]\s*(?P<end>{_NUMBER})"
    rf"(?:\s*(?P<unit>{_alias_pattern()}))?"
    r"(?![\w/²³^])"
)
IPV4_ENDPOINT = re.compile(
    r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?:(?:/\d{1,2})|(?::\d{1,5}))?(?!\d|\.\d)"
)
IPV6_INTERFACE = re.compile(
    r"(?i)(?<![\w:])(?=[0-9a-f:]*:)[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,}"
    r"(?:/\d{1,3})?(?![\w:])"
)


def _pack(language: str) -> StructuredSpeechPack:
    code = language.strip().lower().replace("_", "-").split("-", 1)[0]
    return STRUCTURED_SPEECH_PACKS[code]


def verbalize_measurement(value: str, language: str) -> str | None:
    match = MEASUREMENT.fullmatch(value)
    if not match:
        return None
    pack = _pack(language)
    alias = match.group("unit")
    unit = next(
        (entry for entry in pack.units if alias in entry.aliases),
        None,
    )
    if unit is None:
        return None
    raw_number = match.group("value")
    try:
        quantity = Decimal(raw_number.replace(",", "."))
    except InvalidOperation:
        return None
    category = pack.plural_rule(quantity)
    words = pack.quantity_rule(raw_number, unit)
    return f"{words} {unit.forms.get(category, unit.forms['many'])}"


def verbalize_numeric_range(value: str, language: str) -> str | None:
    """Render dash ranges without feeding ambiguous punctuation to TTS.

    ``minimum X, maximum Y`` deliberately avoids language-specific numeric
    case inflection after words such as Polish ``od``/``do``. The values can
    therefore remain ordinary cardinals in every locale pack.
    """

    match = NUMERIC_RANGE.fullmatch(value)
    if not match:
        return None
    pack = _pack(language)
    start = match.group("start")
    end = match.group("end")
    suffix = ""
    alias = match.group("unit")
    if alias:
        unit = next(
            (entry for entry in pack.units if alias in entry.aliases),
            None,
        )
        if unit is None:
            return None
        try:
            quantity = Decimal(end.replace(",", "."))
        except InvalidOperation:
            return None
        suffix = " " + unit.forms.get(pack.plural_rule(quantity), unit.forms["many"])
        start = pack.quantity_rule(start, unit)
        end = pack.quantity_rule(end, unit)
    return f"{pack.minimum} {start}, {pack.maximum} {end}{suffix}"


def verbalize_ipv4(value: str, language: str) -> str | None:
    pack = _pack(language)
    address_part = value
    cidr: str | None = None
    port: str | None = None
    if "/" in value:
        address_part, cidr = value.split("/", 1)
    elif ":" in value:
        address_part, port = value.rsplit(":", 1)
    try:
        ipaddress.ip_address(address_part)
        if cidr is not None:
            ipaddress.ip_interface(f"{address_part}/{cidr}")
        if port is not None and not 0 <= int(port) <= 65535:
            return None
    except ValueError:
        return None
    spoken = f"{pack.address} " + f" {pack.dot} ".join(address_part.split("."))
    if cidr is not None:
        spoken += f", {pack.prefix} {cidr}"
    if port is not None:
        spoken += f", {pack.port} {port}"
    return spoken


_HEX_LETTERS = {
    "pl": {"a": "a", "b": "be", "c": "ce", "d": "de", "e": "e", "f": "ef"},
    "en": {"a": "a", "b": "b", "c": "c", "d": "d", "e": "e", "f": "f"},
}


def _hex_group(value: str, language: str) -> str:
    letters = _HEX_LETTERS[language]
    return " ".join(letters.get(character, character) for character in value.casefold())


def verbalize_ipv6(value: str, language: str) -> str | None:
    pack = _pack(language)
    try:
        interface = ipaddress.ip_interface(value)
    except ValueError:
        return None
    if interface.version != 6:
        return None
    compressed = interface.ip.compressed
    if "::" in compressed:
        left, right = compressed.split("::", 1)
        left_spoken = f" {pack.colon} ".join(
            _hex_group(group, pack.code) for group in left.split(":") if group
        )
        right_spoken = f" {pack.colon} ".join(
            _hex_group(group, pack.code) for group in right.split(":") if group
        )
        address = f" {pack.double_colon} ".join(
            part for part in (left_spoken, right_spoken) if part
        )
    else:
        address = f" {pack.colon} ".join(
            _hex_group(group, pack.code) for group in compressed.split(":")
        )
    spoken = f"{pack.ipv6_address} {address}"
    if "/" in value:
        spoken += f", {pack.prefix} {interface.network.prefixlen}"
    return spoken
