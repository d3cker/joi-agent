"""Shared, locale-driven preprocessing and dispatch for spoken numbers.

The engine owns syntax that is common across languages (digit grouping and
ordered rewrite phases).  A language pack supplies separators, contextual
abbreviations and the actual grammar verbalizer.  Adding a locale therefore
does not require changing the Markdown renderer or the TTS adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Mapping, Pattern


RewriteValue = str | Callable[[re.Match[str]], str]
NumberNormalizer = Callable[[str], str]


_UNICODE_MINUS = str.maketrans({"\u2212": "-", "\ufe63": "-", "\uff0d": "-"})
_DASH_BEFORE_NUMBER = re.compile(
    r"(?P<prefix>^|[\s([{,:;])(?P<dash>[\u2013\u2014])(?=\d)"
)


@dataclass(frozen=True, slots=True)
class RegexRewrite:
    """A language-pack rewrite applied before numeric token recognition."""

    pattern: Pattern[str]
    replacement: RewriteValue


@dataclass(frozen=True, slots=True)
class LocaleNumberSyntax:
    """Declarative surface syntax used by the shared preprocessing engine."""

    code: str
    grouping_separators: tuple[str, ...]
    contextual_rewrites: tuple[RegexRewrite, ...] = ()
    sentence_abbreviations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NumberLanguagePack:
    """A complete pluggable language entry used by the speech pipeline."""

    syntax: LocaleNumberSyntax
    normalize: NumberNormalizer


def prepare_numeric_text(text: str, syntax: LocaleNumberSyntax) -> str:
    """Apply locale rewrites and collapse valid thousands groups.

    Only groups made of a one-to-three digit head followed by one or more
    three-digit chunks are collapsed.  Ordinary whitespace between unrelated
    integers is left untouched.
    """

    text = canonicalize_numeric_signs(text)
    for rule in syntax.contextual_rewrites:
        text = rule.pattern.sub(rule.replacement, text)
    if not syntax.grouping_separators:
        return text

    alternatives = "|".join(
        sorted((re.escape(value) for value in syntax.grouping_separators), key=len, reverse=True)
    )
    grouped = re.compile(
        rf"(?<![\w\d])(?P<sign>[+-]?)(?P<head>\d{{1,3}})"
        rf"(?P<tail>(?:(?:{alternatives})\d{{3}})+)(?!\d)"
    )
    separators = set(syntax.grouping_separators)

    def collapse(match: re.Match[str]) -> str:
        tail = "".join(
            character for character in match.group("tail") if character not in separators
        )
        return f"{match.group('sign')}{match.group('head')}{tail}"

    return grouped.sub(collapse, text)


def canonicalize_numeric_signs(text: str) -> str:
    """Normalize Unicode minus-like characters only when they mean a sign.

    U+2212 is an unambiguous mathematical minus. En/em dashes are converted
    only when they directly introduce a number. This preserves prose dashes and
    numeric ranges such as ``10–20`` while accepting copy/pasted negative
    values such as ``–170``.
    """

    text = text.translate(_UNICODE_MINUS)

    def replace(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}-"

    return _DASH_BEFORE_NUMBER.sub(replace, text)


class NumberNormalizerRegistry:
    """Immutable-at-runtime dispatch table for locale number packs."""

    def __init__(self, packs: Mapping[str, NumberLanguagePack]) -> None:
        self._packs = {
            code.strip().lower().split("-", 1)[0]: pack
            for code, pack in packs.items()
        }

    @property
    def languages(self) -> tuple[str, ...]:
        return tuple(sorted(self._packs))

    def syntax(self, language: str) -> LocaleNumberSyntax:
        code = language.strip().lower().replace("_", "-").split("-", 1)[0]
        try:
            return self._packs[code].syntax
        except KeyError as exc:
            supported = ", ".join(self.languages)
            raise ValueError(
                f"no number normalizer for {language!r}; supported: {supported}"
            ) from exc

    def normalize(self, text: str, language: str) -> str:
        code = language.strip().lower().replace("_", "-").split("-", 1)[0]
        try:
            normalizer = self._packs[code].normalize
        except KeyError as exc:
            supported = ", ".join(self.languages)
            raise ValueError(
                f"no number normalizer for {language!r}; supported: {supported}"
            ) from exc
        return normalizer(text)

    def prepare(self, text: str, language: str) -> str:
        """Run shared syntax preparation without verbalizing the numbers yet."""

        return prepare_numeric_text(text, self.syntax(language))
