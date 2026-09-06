import pytest

from voice_agent.polish_numbers import (
    expand_polish_numbers,
    integer_to_polish,
    ordinal_to_polish,
    year_to_polish,
)


def test_integer_to_polish_handles_year_and_scale_forms():
    assert integer_to_polish(0) == "zero"
    assert integer_to_polish(1969) == "tysiąc dziewięćset sześćdziesiąt dziewięć"
    assert integer_to_polish(2002) == "dwa tysiące dwa"
    assert integer_to_polish(22_000) == "dwadzieścia dwa tysiące"
    assert integer_to_polish(25_000) == "dwadzieścia pięć tysięcy"
    assert integer_to_polish(2_000_000) == "dwa miliony"


def test_expand_polish_numbers_handles_decimals_percent_and_dotted_values():
    assert expand_polish_numbers("12.75 oraz 1,05") == (
        "dwanaście przecinek siedem pięć oraz jeden przecinek zero pięć"
    )
    assert expand_polish_numbers("35%") == "trzydzieści pięć procent"
    assert expand_polish_numbers("0.2.1") == "zero kropka dwa kropka jeden"


@pytest.mark.parametrize("separator", [" ", "\u00a0", "\u202f"])
def test_grouped_integers_are_parsed_as_one_value(separator):
    source = f"Cena: 296{separator}700; obrót: 1{separator}234{separator}567,89."
    assert expand_polish_numbers(source) == (
        "Cena: dwieście dziewięćdziesiąt sześć tysięcy siedemset; "
        "obrót: milion dwieście trzydzieści cztery tysiące pięćset "
        "sześćdziesiąt siedem przecinek osiem dziewięć."
    )


def test_currency_symbols_and_codes_use_language_pack_forms():
    assert expand_polish_numbers("1 zł, 2 PLN, 5 USD") == (
        "jeden złoty, dwa złote, pięć dolarów"
    )
    assert expand_polish_numbers("$296 700") == (
        "dwieście dziewięćdziesiąt sześć tysięcy siedemset dolarów"
    )


def test_invalid_grouping_is_not_merged_and_approximation_is_contextual():
    assert expand_polish_numbers("12 34") == "dwanaście trzydzieści cztery"
    assert expand_polish_numbers("ok. 200 000 zł") == "około dwieście tysięcy złotych"
    assert expand_polish_numbers("OK. 200 000 osób") == "OK. dwieście tysięcy osób"


def test_invalid_date_is_left_as_numbers_not_misnamed_months():
    assert expand_polish_numbers("45.19.2026") == (
        "czterdzieści pięć kropka dziewiętnaście kropka dwa tysiące dwadzieścia sześć"
    )


def test_day_month_date_without_year_uses_genitive_ordinal():
    assert expand_polish_numbers("Spotkanie: 26.08.") == (
        "Spotkanie: dwudziestego szóstego sierpnia."
    )
    assert expand_polish_numbers("Spotkanie: 05/09.") == (
        "Spotkanie: piątego września."
    )


def test_non_zero_padded_decimal_is_not_misclassified_as_date():
    assert expand_polish_numbers("Wynik: 3.14.") == (
        "Wynik: trzy przecinek jeden cztery."
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1 stycznia", "pierwszego stycznia"),
        ("2 lutego", "drugiego lutego"),
        ("3 marca", "trzeciego marca"),
        ("12 kwietnia", "dwunastego kwietnia"),
        ("21 maja", "dwudziestego pierwszego maja"),
        ("26 sierpnia", "dwudziestego szóstego sierpnia"),
        ("30 listopada", "trzydziestego listopada"),
        ("31 grudnia", "trzydziestego pierwszego grudnia"),
        ("26-go sierpnia", "dwudziestego szóstego sierpnia"),
    ],
)
def test_calendar_day_before_polish_month_uses_genitive_ordinal(source, expected):
    assert expand_polish_numbers(source) == expected


def test_non_date_numbers_and_invalid_calendar_days_remain_cardinal():
    assert expand_polish_numbers("Mam 26 plików") == "Mam dwadzieścia sześć plików"
    assert expand_polish_numbers("32 sierpnia") == "trzydzieści dwa sierpnia"


def test_every_valid_calendar_day_has_a_genitive_form():
    expected = (
        "pierwszego", "drugiego", "trzeciego", "czwartego", "piątego",
        "szóstego", "siódmego", "ósmego", "dziewiątego", "dziesiątego",
        "jedenastego", "dwunastego", "trzynastego", "czternastego",
        "piętnastego", "szesnastego", "siedemnastego", "osiemnastego",
        "dziewiętnastego", "dwudziestego", "dwudziestego pierwszego",
        "dwudziestego drugiego", "dwudziestego trzeciego",
        "dwudziestego czwartego", "dwudziestego piątego",
        "dwudziestego szóstego", "dwudziestego siódmego",
        "dwudziestego ósmego", "dwudziestego dziewiątego", "trzydziestego",
        "trzydziestego pierwszego",
    )

    for day, ordinal in enumerate(expected, start=1):
        assert expand_polish_numbers(f"{day} sierpnia") == f"{ordinal} sierpnia"


@pytest.mark.parametrize(
    ("value", "case", "expected"),
    [
        (6, "nominative", "szósty"),
        (26, "genitive", "dwudziestego szóstego"),
        (69, "locative", "sześćdziesiątym dziewiątym"),
        (100, "genitive", "setnego"),
        (969, "genitive", "dziewięćset sześćdziesiątego dziewiątego"),
        (2000, "genitive", "dwutysięcznego"),
        (2026, "genitive", "dwa tysiące dwudziestego szóstego"),
    ],
)
def test_ordinal_builder_composes_cases(value, case, expected):
    assert ordinal_to_polish(value, case) == expected


def test_year_inflection_uses_sentence_context():
    assert year_to_polish(1969, "nominative") == (
        "tysiąc dziewięćset sześćdziesiąty dziewiąty"
    )
    assert expand_polish_numbers("W 1969 roku") == (
        "W tysiąc dziewięćset sześćdziesiątym dziewiątym roku"
    )
    assert expand_polish_numbers("Rok 1969") == (
        "Rok tysiąc dziewięćset sześćdziesiąty dziewiąty"
    )


def test_complete_named_date_selects_case_from_grammatical_context():
    assert expand_polish_numbers(
        "Dziś jest niedziela, 6 września 2026 roku."
    ) == (
        "Dziś jest niedziela, szósty września dwa tysiące "
        "dwudziestego szóstego roku."
    )
    assert expand_polish_numbers(
        "Spotkanie odbędzie się 6 września 2026 roku."
    ) == (
        "Spotkanie odbędzie się szóstego września dwa tysiące "
        "dwudziestego szóstego roku."
    )
    assert expand_polish_numbers(
        "Rozumiem, dzisiaj jest niedziela, 6 września 2026 roku."
    ) == (
        "Rozumiem, dzisiaj jest niedziela, szósty września dwa tysiące "
        "dwudziestego szóstego roku."
    )
