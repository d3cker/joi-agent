import re

import pytest

from voice_agent.number_normalization import (
    LocaleNumberSyntax,
    NumberLanguagePack,
    NumberNormalizerRegistry,
    RegexRewrite,
    prepare_numeric_text,
    canonicalize_numeric_signs,
)


def test_shared_preprocessor_is_driven_by_locale_syntax():
    syntax = LocaleNumberSyntax(
        code="test",
        grouping_separators=("'",),
        contextual_rewrites=(
            RegexRewrite(re.compile(r"~(?=\d)"), "approximately "),
        ),
    )

    assert prepare_numeric_text("~12'345'678", syntax) == "approximately 12345678"
    assert prepare_numeric_text("12'34", syntax) == "12'34"


def test_registry_accepts_an_additional_language_without_renderer_branches():
    en = LocaleNumberSyntax(code="en", grouping_separators=(",",))
    custom = LocaleNumberSyntax(code="xx", grouping_separators=("'",))
    registry = NumberNormalizerRegistry({
        "en": NumberLanguagePack(en, lambda text: f"english:{text}"),
        "xx": NumberLanguagePack(custom, lambda text: f"custom:{text}"),
    })

    assert registry.normalize("42", "xx-XX") == "custom:42"
    assert registry.languages == ("en", "xx")
    with pytest.raises(ValueError, match="no number normalizer"):
        registry.normalize("42", "pl")


def test_unicode_numeric_signs_do_not_corrupt_ranges_or_prose_dashes():
    assert canonicalize_numeric_signs("−170 –170 — 42") == "-170 -170 — 42"
    assert canonicalize_numeric_signs("10–20 and A — B") == "10–20 and A — B"
