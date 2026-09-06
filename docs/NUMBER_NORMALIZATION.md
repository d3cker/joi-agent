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

## Measurement vocabulary

`voice_agent/unit_catalog.py` contains 110 additional declarative unit entries,
bringing the PL and EN packs to 143 units and 234 distinct aliases each. Each
row specifies case-sensitive aliases, Polish singular/few/many/fraction forms,
English singular/plural forms, and Polish grammatical gender. The original
core vocabulary and recognition engine remain in `structured_values.py`.
These are versioned source data, not backend settings or a pronunciation file;
changing them requires deploying/restarting the backend, not rebuilding the Mac.

| Family | Examples of accepted symbols |
| --- | --- |
| Pressure | Pa, hPa, kPa, MPa, GPa, bar, mbar, atm, Torr, mmHg, inHg, psi |
| Area | ha, a, ar, ac, ft², in², yd², mi², mm², dm² |
| Length | mi, nmi, ft, in, inch, yd, dm, µm, μm, um, nm, Å |
| Mass | t, kt, lb, lbs, oz, st, mg, µg, mcg, ng, dag, dkg |
| Volume | L, l, mL, cL, dL, hL, µL, cm³, dm³, ft³, in³, gal, US gal, imp gal, qt, pt, fl oz, bbl |
| Energy and force | J, kJ, MJ, GJ, cal, kcal, Wh, MWh, GWh, hp, N, kN, N·m |
| Electricity | mW, MW, GW, mV, kV, mA, µA, Ah, mAh, Ω, kΩ, MΩ, F, µF, nF, pF, H, mH |
| Other physics | K, lm, lx, cd, dB, mol, mmol, Bq, Gy, Sv, mSv, µSv |
| Rates and durations | mph, kn, ft/s, m/s², L/min, L/s, rpm, obr/min, min, µs, ns |

The engine recognizes complete values before identifier/symbol spelling. It
does not convert quantities or silently choose a US/Imperial volume definition.
Prefixes are explicit: `mW` and `MW` differ, and unknown compound units must
not be partially matched. New squared/cubed entries accept superscripts and
explicit ASCII aliases such as `ft2` and `ft^2`. This is not a general-purpose
dimensional-expression parser.

Ambiguous bare `a` and `in` require a terminal measurement context (end or
punctuation); use `ar`/`inch` within ambiguous prose. Stone `st` requires a
space so `1st` remains an English ordinal. Bare `pm` and `us` are intentionally
not introduced as unit aliases because they collide with common time/prose.
Likewise, quote marks are not interpreted as feet/inches. Clock-time grammar
is separate and is not implemented by this vocabulary expansion.

Examples: `2 mi` becomes `dwie mile`, `1.5 ha` becomes
`jeden przecinek pięć hektara`, and `1013 hPa` becomes
`tysiąc trzynaście hektopaskali`. Only speech input changes; visible Markdown
and session history stay original. `test_units.py` exercises every new alias
through the real renderer in both languages, every new entry's quantity forms,
case collisions, Unicode spaces, ranges, tables and trailing prose. These are
text-pipeline tests, not evidence of a physical TTS listening test.

The streaming renderer also defers a trailing digit plus decimal separator
until a following character or final flush arrives. Otherwise an SSE split
between `1.` and `5 lb` could commit the integer as a sentence and suppress
the remaining speech after the rendered prefix changes.

Symbol references: [BIPM SI prefixes](https://www.bipm.org/en/measurement-units/si-prefixes)
and [NIST unit tables](https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors/nist-guide-si-appendix-b9).

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
