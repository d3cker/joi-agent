from voice_agent.english_numbers import (
    expand_english_numbers,
    integer_to_english,
    ordinal_to_english,
    year_to_english,
)


def test_integer_and_ordinal_words_cover_list_ranges():
    assert integer_to_english(0) == "zero"
    assert integer_to_english(42) == "forty-two"
    assert integer_to_english(1_969) == "one thousand nine hundred sixty-nine"
    assert ordinal_to_english(1) == "first"
    assert ordinal_to_english(22) == "twenty-second"
    assert ordinal_to_english(100) == "one hundredth"


def test_years_are_spoken_using_english_convention():
    assert year_to_english(1969) == "nineteen sixty-nine"
    assert year_to_english(2005) == "two thousand five"
    assert year_to_english(2026) == "twenty twenty-six"


def test_expansion_handles_dates_decimals_versions_percent_and_negatives():
    assert expand_english_numbers("05.09.2026") == (
        "September fifth, twenty twenty-six"
    )
    assert expand_english_numbers("2026-09-05") == (
        "September fifth, twenty twenty-six"
    )
    assert expand_english_numbers("v 2.3.4; -12.50; 98.5%") == (
        "v two point three point four; minus twelve point five zero; "
        "ninety-eight point five percent"
    )


def test_grouped_values_are_parsed_before_integer_verbalization():
    assert expand_english_numbers("The price is 296,700 dollars.") == (
        "The price is two hundred ninety-six thousand seven hundred dollars."
    )
    assert expand_english_numbers("Users: 1\u202f234\u202f567.89") == (
        "Users: one million two hundred thirty-four thousand five hundred "
        "sixty-seven point eight nine"
    )
    assert expand_english_numbers("approx. 200,000 users") == (
        "approximately two hundred thousand users"
    )
    assert expand_english_numbers("$296,700 and 1 USD") == (
        "two hundred ninety-six thousand seven hundred dollars and one dollar"
    )


def test_year_reading_requires_year_or_date_context():
    assert expand_english_numbers("In 1969, it launched.") == (
        "In nineteen sixty-nine, it launched."
    )
    assert expand_english_numbers("There are 1,969 files.") == (
        "There are one thousand nine hundred sixty-nine files."
    )


def test_named_english_date_orders_share_one_calendar_rule():
    expected = "September sixth, twenty twenty-six."
    assert expand_english_numbers("September 6, 2026.") == expected
    assert expand_english_numbers("6 September 2026.") == expected
