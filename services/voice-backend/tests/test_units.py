"""Measurement coverage through the real Markdown-to-speech path, without GPU."""
import pytest

from voice_agent.speech_text import MarkdownSpeechRenderer
from voice_agent.structured_values import MEASUREMENT, STRUCTURED_SPEECH_PACKS
from voice_agent.unit_catalog import UNIT_CATALOG


@pytest.mark.parametrize("language", ["pl", "en"])
def test_catalog_symbols_are_unique_and_locales_have_matching_coverage(language):
    units = STRUCTURED_SPEECH_PACKS[language].units
    aliases = [alias for unit in units for alias in unit.aliases]
    assert len(aliases) == len(set(aliases))
    assert len(units) >= 140
    assert {u.aliases for u in units} == {
        u.aliases for u in STRUCTURED_SPEECH_PACKS["en"].units
    }


@pytest.mark.parametrize("row", UNIT_CATALOG, ids=lambda row: row[0])
@pytest.mark.parametrize("language", ["pl", "en"])
def test_every_new_alias_survives_full_renderer_with_localized_words(row, language):
    aliases, polish, english, _ = row
    unit_words = polish.split("|")[2] if language == "pl" else english.split("|")[1]
    number = "pięć" if language == "pl" else "five"
    for alias in aliases.split("|"):
        assert MarkdownSpeechRenderer(language).render(f"5 {alias}.") == f"{number} {unit_words}."


@pytest.mark.parametrize("row", UNIT_CATALOG, ids=lambda row: row[0])
def test_every_new_unit_has_integer_fraction_and_gender_agreement(row):
    aliases, polish, english, gender = row
    one, few, many, fraction = polish.split("|")
    en_one, en_many = english.split("|")
    symbol = aliases.split("|")[0]
    samples = [
        ("0", "zero", many, "zero", en_many),
        ("1", "jedna" if gender == "f" else "jeden", one, "one", en_one),
        ("2", "dwie" if gender == "f" else "dwa", few, "two", en_many),
        ("12", "dwanaście", many, "twelve", en_many),
        ("22", "dwadzieścia dwie" if gender == "f" else "dwadzieścia dwa", few, "twenty-two", en_many),
        ("1.5", "jeden przecinek pięć", fraction, "one point five", en_many),
    ]
    for value, pl_number, pl_unit, en_number, en_unit in samples:
        assert MarkdownSpeechRenderer("pl").render(f"{value} {symbol}.") == f"{pl_number} {pl_unit}."
        assert MarkdownSpeechRenderer("en").render(f"{value} {symbol}.") == f"{en_number} {en_unit}."


@pytest.mark.parametrize("text,polish,english", [
    ("1013 hPa", "tysiąc trzynaście hektopaskali", "one thousand thirteen hectopascals"),
    ("1 ha", "jeden hektar", "one hectare"),
    ("2 ha", "dwa hektary", "two hectares"),
    ("12 ha", "dwanaście hektarów", "twelve hectares"),
    ("22 ha", "dwadzieścia dwa hektary", "twenty-two hectares"),
    ("1.5 ha", "jeden przecinek pięć hektara", "one point five hectares"),
    ("1 a", "jeden ar", "one are"),
    ("2 a", "dwa ary", "two ares"),
    ("5 ac", "pięć akrów", "five acres"),
    ("1 mi", "jedna mila", "one mile"),
    ("2 mi", "dwie mile", "two miles"),
    ("12 mi", "dwanaście mil", "twelve miles"),
    ("22 mi", "dwadzieścia dwie mile", "twenty-two miles"),
    ("102 mi", "sto dwie mile", "one hundred two miles"),
    ("-2 ft", "minus dwie stopy", "minus two feet"),
    ("1.5 ft", "jeden przecinek pięć stopy", "one point five feet"),
    ("2 lb", "dwa funty", "two pounds"),
    ("5 lb", "pięć funtów", "five pounds"),
    ("2 fl oz", "dwie uncje płynu", "two fluid ounces"),
    ("2 mW", "dwa miliwaty", "two milliwatts"),
    ("2 MW", "dwa megawaty", "two megawatts"),
    ("2 mAh", "dwie miliamperogodziny", "two milliampere-hours"),
    ("4 ft²", "cztery stopy kwadratowe", "four square feet"),
    ("2–4 ft", "minimum dwie, maksimum cztery stopy", "minimum two, maximum four feet"),
])
def test_quantity_grammar_golden_cases(text, polish, english):
    assert MarkdownSpeechRenderer("pl").render(text) == polish + "."
    assert MarkdownSpeechRenderer("en").render(text) == english + "."


@pytest.mark.parametrize("text", [
    "2 a potem 3", "5 in stock", "5 pm today", "5 us citizens",
    "2 hPaSensor", "2 footprint", "2 mi/h", "2 ha/path", "2 Pa^4",
    "abc2ha", "v2ha", "2 HA", "2 hpa", "2 ft4", "1st", "21st", "5pm", "5 pm",
])
def test_symbols_do_not_match_words_unsupported_compounds_or_wrong_case(text):
    assert MEASUREMENT.search(text) is None


def test_unicode_spacing_attached_symbols_and_markdown_preserve_all_content():
    renderer = MarkdownSpeechRenderer("pl")
    assert renderer.render("**1013hPa**, (2\u00a0ha), 1,5\u202fft.") == (
        "tysiąc trzynaście hektopaskali, dwa hektary, jeden przecinek pięć stopy."
    )


def test_spelling_units_never_converts_values_or_guesses_volume_system():
    renderer = MarkdownSpeechRenderer("en")
    assert renderer.render("1 US gal; 1 imp gal; 1 gal.") == (
        "one US gallon; one imperial gallon; one gallon."
    )


def test_markdown_table_reads_measurements_and_trailing_sentence():
    text = "| Area | Pressure |\n| --- | --- |\n| 2 ha | 1013 hPa |\n\nDistance: 5 mi."
    for language, words in [("pl", ["dwa hektary", "tysiąc trzynaście hektopaskali", "pięć mil"]),
                            ("en", ["two hectares", "one thousand thirteen hectopascals", "five miles"])]:
        spoken = MarkdownSpeechRenderer(language).render(text)
        for value in words:
            assert value in spoken
        assert spoken.endswith(words[-1] + ".")


@pytest.mark.parametrize("language", ["pl", "en"])
@pytest.mark.parametrize("chunk_size", [1, 3, 11])
def test_streamed_symbols_and_markdown_match_complete_speech(language, chunk_size):
    from voice_agent.session import StreamingMarkdownSpeechSegmenter

    renderer = MarkdownSpeechRenderer(language)
    segmenter = StreamingMarkdownSpeechSegmenter(
        renderer, soft_limit=300, hard_limit=400, max_words=60,
    )
    text = "**1013 hPa**; 2 ha; 5 mi; 2 ft². 1.5 lb; 2 fl oz."
    parts = []
    for offset in range(0, len(text), chunk_size):
        parts.extend(segmenter.push(text[offset:offset + chunk_size]))
    parts.extend(segmenter.flush())
    assert " ".join(parts) == renderer.render(text)


@pytest.mark.parametrize("language", ["pl", "en"])
def test_terminal_number_still_flushes_and_new_sentence_is_not_lost(language):
    from voice_agent.session import StreamingMarkdownSpeechSegmenter

    def segmenter():
        return StreamingMarkdownSpeechSegmenter(
            MarkdownSpeechRenderer(language), soft_limit=300, hard_limit=400, max_words=60,
        )

    final = segmenter()
    assert final.push("Value: 1.") == []
    assert final.flush() == [MarkdownSpeechRenderer(language).render("Value: 1.")]
    continued = segmenter()
    assert continued.push("Value: 1.") == []
    parts = continued.push(" Next: 2 ha.") + continued.flush()
    assert " ".join(parts) == MarkdownSpeechRenderer(language).render("Value: 1. Next: 2 ha.")
