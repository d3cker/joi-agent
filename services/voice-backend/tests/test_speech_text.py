from voice_agent.speech_text import MarkdownSpeechRenderer


def test_markdown_is_rendered_as_spoken_prose():
    source = """# Daily report

- **Revenue** increased by [ten percent](https://example.com/report).
- See <https://status.example.com> for details.

```python
print("this code must not be spoken")
```
"""

    spoken = MarkdownSpeechRenderer().render(source)

    assert spoken == (
        "Daily report. Revenue increased by ten percent. "
        "See status kropka example kropka com for details."
    )
    assert "https://" not in spoken
    assert "```" not in spoken
    assert "print" not in spoken
    assert "**" not in spoken


def test_table_markup_and_images_do_not_leak_to_speech():
    source = """| Model | Status |
|---|---|
| Higgs | ready |

![chart](chart.png)
"""

    spoken = MarkdownSpeechRenderer().render(source)

    assert spoken == "Model: Higgs. Status: ready."
    assert "chart.png" not in spoken
    assert "|" not in spoken


def test_table_rows_are_spoken_with_headers_and_trailing_prose_is_preserved():
    source = """Oto lista umiejętności:

| Slug | Nazwa | Opis | Wersja |
|---|---|---|---|
| open-terminal-work | Open Terminal Work | Przegląda pliki i uruchamia kod. | 1 |
| web-research | Web research | Wyszukuje przez SearXNG. | 1 |

Dostępne są dwie umiejętności.
"""

    spoken = MarkdownSpeechRenderer().render(source)

    assert "Slug: open terminal work" in spoken
    assert "Opis: Przegląda pliki i uruchamia kod." in spoken
    assert "Slug: web research" in spoken
    assert spoken.endswith("Dostępne są dwie umiejętności.")
    assert "---" not in spoken


def test_standard_polish_abbreviations_are_expanded_before_segmentation():
    source = (
        "Sprawdź **np.** modele, itp. dodatki i m.in. wyniki dr Kowalskiego "
        "oraz prof. Nowak. Numer zgłoszenia: nr 12."
    )

    spoken = MarkdownSpeechRenderer().render(source)

    assert spoken == (
        "Sprawdź na przykład modele, i tym podobne dodatki i między innymi "
        "wyniki doktor Kowalskiego oraz profesor Nowak. "
        "Numer zgłoszenia: numer dwanaście."
    )


def test_abbreviation_expansion_does_not_modify_url_text():
    source = "Otwórz https://example.com/np.test i ustaw wartość 12.75. OK."

    spoken = MarkdownSpeechRenderer().render(source)

    assert "example kropka com" in spoken
    assert "dwanaście przecinek siedem pięć" in spoken
    assert "OK" in spoken


def test_numbered_and_bulleted_markdown_do_not_create_double_periods():
    source = (
        "1. Nagrywa głos przez mikrofon.  \n"
        "2. Zamienia mowę na tekst.\n\n"
        "- Przetwarza tekst lokalnym modelem AI.  \n"
        "- Odpowiada głosem.\n"
    )

    spoken = MarkdownSpeechRenderer().render(source)

    assert spoken == (
        "Po pierwsze, Nagrywa głos przez mikrofon. "
        "Po drugie, Zamienia mowę na tekst. "
        "Przetwarza tekst lokalnym modelem AI. Odpowiada głosem."
    )
    assert ".." not in spoken


def test_deliberate_ellipsis_is_preserved():
    spoken = MarkdownSpeechRenderer().render("Poczekaj... dobrze.  ")

    assert spoken == "Poczekaj... dobrze."


def test_abbreviations_next_to_parentheses_are_normalized_directly():
    spoken = MarkdownSpeechRenderer().render(
        "Model (np. lokalny), dodatki itp., autorzy m.in. dr Kowalski i prof. Nowak."
    )

    assert spoken == (
        "Model, na przykład lokalny, dodatki i tym podobne, autorzy między innymi "
        "doktor Kowalski i profesor Nowak."
    )


def test_polish_bold_parentheses_numbers_and_emoji_are_safe_for_tts():
    source = (
        "**W 1969 roku** uruchomiono misję (to ważna data) 🚀. "
        "Skuteczność wyniosła 98.5%."
    )

    spoken = MarkdownSpeechRenderer().render(source)

    assert spoken == (
        "W tysiąc dziewięćset sześćdziesiątym dziewiątym roku uruchomiono misję, "
        "to ważna data. Skuteczność wyniosła dziewięćdziesiąt osiem przecinek "
        "pięć procent."
    )
    assert "**" not in spoken
    assert "(" not in spoken and ")" not in spoken
    assert "🚀" not in spoken


def test_nonverbal_stage_directions_and_emoji_only_blocks_are_not_spoken():
    source = "Programista (wzdycha): Dobrze.\n\n😄\n\nDo usłyszenia 👋."

    spoken = MarkdownSpeechRenderer().render(source)

    assert spoken == "Programista: Dobrze. Do usłyszenia."
    assert "wzdycha" not in spoken


def test_common_date_formats_are_spoken_in_polish():
    renderer = MarkdownSpeechRenderer()

    assert renderer.render("Data: 05.09.2026.") == (
        "Data: piąty września dwa tysiące dwudziestego szóstego."
    )
    assert renderer.render("Data: 2026-09-05.") == (
        "Data: piąty września dwa tysiące dwudziestego szóstego."
    )


def test_written_polish_month_makes_day_a_genitive_ordinal():
    renderer = MarkdownSpeechRenderer("pl")

    assert renderer.render("Spotkanie odbędzie się 26 sierpnia.") == (
        "Spotkanie odbędzie się dwudziestego szóstego sierpnia."
    )
    assert renderer.render("Mam 26 plików do sprawdzenia.") == (
        "Mam dwadzieścia sześć plików do sprawdzenia."
    )


def test_zero_padded_polish_day_month_without_year_uses_genitive_ordinal():
    renderer = MarkdownSpeechRenderer("pl")

    assert renderer.render("Spotkanie odbędzie się 26.08.") == (
        "Spotkanie odbędzie się dwudziestego szóstego sierpnia."
    )


def test_english_numbers_and_decimals_are_expanded_in_english():
    spoken = MarkdownSpeechRenderer("en").render(
        "**In 1969**, the measured value was (3.14) and efficiency was 98.5%."
    )

    assert spoken == (
        "In nineteen sixty-nine, the measured value was, three point one four, "
        "and efficiency was ninety-eight point five percent."
    )
    assert "tysiąc" not in spoken
    assert "przecinek" not in spoken


def test_switching_renderer_back_to_polish_restores_polish_numbers():
    assert MarkdownSpeechRenderer("en").render("Year 1969.") == (
        "Year nineteen sixty-nine."
    )
    assert MarkdownSpeechRenderer("pl").render("Rok 1969.") == (
        "Rok tysiąc dziewięćset sześćdziesiąty dziewiąty."
    )


def test_english_ordered_and_plain_numbered_lists_use_english_ordinals():
    renderer = MarkdownSpeechRenderer("en")

    assert renderer.render("1. Install the package.\n2. Run the test.\n3. Read the result.") == (
        "First, Install the package. Second, Run the test. Third, Read the result."
    )
    assert renderer.render("1, Install it.\n2, Run it.\n3, Read it.") == (
        "First, Install it. Second, Run it. Third, Read it."
    )


def test_polish_ordered_list_uses_polish_ordinals_from_the_same_semantics():
    spoken = MarkdownSpeechRenderer("pl").render(
        "1. Zainstaluj pakiet.\n2. Uruchom test.\n3. Odczytaj wynik."
    )

    assert spoken == (
        "Po pierwsze, Zainstaluj pakiet. Po drugie, Uruchom test. "
        "Po trzecie, Odczytaj wynik."
    )


def test_paths_and_identifiers_never_leak_raw_separators_to_english_tts():
    spoken = MarkdownSpeechRenderer("en").render(
        "Use some_file_name.py at /home/user/bubble_sort_demo.py with --dry-run."
    )

    assert spoken == (
        "Use some file name dot p y at path home, user, bubble sort demo dot p y "
        "with dash dash dry dash run."
    )
    assert all(character not in spoken for character in "_/")


def test_inline_code_uses_exact_separator_names_in_both_languages():
    english = MarkdownSpeechRenderer("en").render(
        "Open `/home/user/some_file.py` and use `key=value`."
    )
    polish = MarkdownSpeechRenderer("pl").render(
        "Otwórz `/home/user/some_file.py` i użyj `key=value`."
    )

    assert english == (
        "Open slash home slash user slash some underscore file dot p y "
        "and use key equals value."
    )
    assert polish == (
        "Otwórz ukośnik home ukośnik user ukośnik some podkreślenie file "
        "kropka p y i użyj key równa się value."
    )


def test_iso_dates_are_classified_before_kebab_identifiers():
    assert MarkdownSpeechRenderer("en").render("Date: 2026-09-05.") == (
        "Date: September fifth, twenty twenty-six."
    )
    assert MarkdownSpeechRenderer("pl").render("Data: 2026-09-05.") == (
        "Data: piąty września dwa tysiące dwudziestego szóstego."
    )


def test_polish_dates_and_grouped_values_use_contextual_grammar():
    renderer = MarkdownSpeechRenderer("pl")

    assert renderer.render(
        "Dziś jest niedziela, 6 września 2026 roku."
    ) == (
        "Dziś jest niedziela, szósty września dwa tysiące "
        "dwudziestego szóstego roku."
    )
    assert renderer.render("Cena BTC to 296 700 zł, czyli ok. 200 000 więcej.") == (
        "Cena BTC to dwieście dziewięćdziesiąt sześć tysięcy siedemset złotych, "
        "czyli około dwieście tysięcy więcej."
    )
    assert renderer.render(
        "Rozumiem, dzisiaj jest niedziela, 6 września 2026 roku."
    ) == (
        "Rozumiem, dzisiaj jest niedziela, szósty września dwa tysiące "
        "dwudziestego szóstego roku."
    )


def test_english_grouped_values_and_named_dates_remain_english():
    renderer = MarkdownSpeechRenderer("en")

    assert renderer.render(
        "On September 6, 2026, the price was approx. 296,700 dollars."
    ) == (
        "On September sixth, twenty twenty-six, the price was approximately "
        "two hundred ninety-six thousand seven hundred dollars."
    )
    assert renderer.render("The price is $296,700.") == (
        "The price is two hundred ninety-six thousand seven hundred dollars."
    )


def test_urls_emails_versions_and_assignments_are_safe_structured_speech():
    spoken = MarkdownSpeechRenderer("en").render(
        "Visit https://example.com/a_b, mail first_last@example.com, "
        "use v2.3.4 and MODE=fast."
    )

    assert "example dot com, a b" in spoken
    assert "first last at example dot com" in spoken
    assert "version two point three point four" in spoken
    assert "MODE equals fast" in spoken
    assert all(character not in spoken for character in "_/@=")


def test_approximation_is_not_reclassified_as_a_list_inside_parentheses():
    polish = MarkdownSpeechRenderer("pl")
    english = MarkdownSpeechRenderer("en")

    assert polish.render("Wynik (ok. 14).") == "Wynik, około czternaście."
    assert polish.render("Wynik to ok. (14).") == "Wynik to około, czternaście."
    assert polish.render("- ok. 14") == "około czternaście."
    assert english.render("Result (approx. 14).") == "Result, approximately fourteen."


def test_ipv4_cidr_and_port_outrank_generic_version_detection():
    polish = MarkdownSpeechRenderer("pl")
    english = MarkdownSpeechRenderer("en")

    assert polish.render("Sieć 10.0.0.1/24.") == (
        "Sieć adres dziesięć kropka zero kropka zero kropka jeden, "
        "prefiks dwadzieścia cztery."
    )
    assert polish.render("Serwer 192.168.30.215:8765.") == (
        "Serwer adres sto dziewięćdziesiąt dwa kropka sto sześćdziesiąt osiem "
        "kropka trzydzieści kropka dwieście piętnaście, port osiem tysięcy "
        "siedemset sześćdziesiąt pięć."
    )
    assert english.render("Network 10.0.0.1/24.") == (
        "Network address ten dot zero dot zero dot one, prefix twenty-four."
    )
    assert "wersja" not in polish.render("Adres 172.16.0.8.").casefold()
    assert english.render("Version v1.2.3.") == "version one point two point three."


def test_temperatures_unicode_minus_and_common_units_are_locale_driven():
    polish = MarkdownSpeechRenderer("pl")
    english = MarkdownSpeechRenderer("en")

    assert polish.render("400°C w dzień do ok. –170°C.") == (
        "czterysta stopni Celsjusza w dzień do około minus sto siedemdziesiąt "
        "stopni Celsjusza."
    )
    assert english.render("400°C by day to approx. –170°C.") == (
        "four hundred degrees Celsius by day to approximately minus one hundred "
        "seventy degrees Celsius."
    )
    assert polish.render("1°C, 2°C, 5°C; 16 GB i 100 km/h.") == (
        "jeden stopień Celsjusza, dwa stopnie Celsjusza, pięć stopni Celsjusza; "
        "szesnaście gigabajtów i sto kilometrów na godzinę."
    )
    assert english.render("1°F, 2°F, 16 GB and 100 km/h.") == (
        "one degree Fahrenheit, two degrees Fahrenheit, sixteen gigabytes and "
        "one hundred kilometers per hour."
    )
    assert polish.render("Łącze 1 Gb/s, opóźnienie 25 ms.") == (
        "Łącze jeden gigabit na sekundę, opóźnienie dwadzieścia pięć milisekund."
    )


def test_malformed_degree_and_unicode_dash_never_reach_tts_raw():
    spoken = MarkdownSpeechRenderer("pl").render(
        "Symbol 12° bez jednostki — pole 4 m²."
    )

    assert spoken == (
        "Symbol dwanaście stopni bez jednostki, pole cztery metry kwadratowe."
    )
    assert all(character not in spoken for character in "°–—−²³")


def test_numeric_ranges_do_not_leak_dash_control_symbols_to_tts():
    assert MarkdownSpeechRenderer("pl").render("Zakres 10–20°C.") == (
        "Zakres minimum dziesięć, maksimum dwadzieścia stopni Celsjusza."
    )
    assert MarkdownSpeechRenderer("en").render("Range 10–20°C.") == (
        "Range minimum ten, maximum twenty degrees Celsius."
    )
