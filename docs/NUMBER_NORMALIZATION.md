# Locale-driven number normalization

The visible assistant response and stored session history are never rewritten.
Number normalization runs only on the prose submitted to TTS.

## Pipeline

1. Markdown is parsed into spoken prose while URLs, paths, versions, network
   addresses, measurements, and code spans are protected.
2. `NumberNormalizerRegistry` selects a `NumberLanguagePack` using the active
   conversation locale.
3. The shared preprocessor applies declarative contextual rewrites and validates
   thousands grouping. Only a one-to-three digit head followed by complete
   three-digit groups is collapsed.
4. Structured-value packs classify validated IPv4/CIDR/port and IPv6 values,
   measurements, temperatures, ranges, and Unicode numeric signs before a
   dotted value can be mistaken for a version or punctuation can reach TTS.
5. The number language pack classifies dates, contextual years, currency
   values, decimals, percentages, ordinals, and remaining integers in
   precedence order.
6. Language grammar generates the final words. Polish composes grammatical
   ordinal cases; English distinguishes contextual years from ordinary values.
7. Sentence segmentation runs with abbreviation boundaries declared by the
   active language pack, so a streamed abbreviation cannot be committed before
   its following number arrives.

Examples:

| Locale | Visible text | TTS-only text |
|---|---|---|
| `pl` | `296 700` | `dwieście dziewięćdziesiąt sześć tysięcy siedemset` |
| `pl` | `ok. 200 000` | `około dwieście tysięcy` |
| `pl` | `(ok. 14)` | `około czternaście` |
| `pl` | `10.0.0.1/24` | `adres dziesięć kropka zero kropka zero kropka jeden, prefiks dwadzieścia cztery` |
| `pl` | `400°C … –170°C` | `czterysta stopni Celsjusza … minus sto siedemdziesiąt stopni Celsjusza` |
| `pl` | `6 września 2026 roku` after a nominative date cue | `szósty września dwa tysiące dwudziestego szóstego roku` |
| `en` | `296,700` | `two hundred ninety-six thousand seven hundred` |
| `en` | `$296,700` | `two hundred ninety-six thousand seven hundred dollars` |
| `en` | `September 6, 2026` | `September sixth, twenty twenty-six` |

## Adding another language

Adding a locale does not require a branch in the Markdown renderer, session
loop, segmenter, or TTS adapter:

1. Define a `LocaleNumberSyntax` with grouping separators, contextual rewrites,
   and sentence abbreviations.
2. Implement one normalizer function using the locale's cardinal, ordinal,
   decimal, calendar, and grammatical rules.
3. Add a `StructuredSpeechPack` with address labels, unit lexemes, plural
   forms, and a locale plural-category function. Recognition order and parsing
   stay shared; only linguistic data changes.
4. Register the number pack in `speech_semantics.py` and the structured pack in
   `structured_values.py`.
5. Add the locale to the conversation-language catalog and provide fixture
   tests covering grouping, invalid grouping, dates, decimals, and streaming
   boundaries.

Vocabulary and inflection tables are linguistic data belonging to a language
pack. Application subjects, brands, concrete prices, and individual calendar
dates must never appear as normalization exceptions.
