from __future__ import annotations

import re
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .languages import conversation_language
from .speech_semantics import (
    ordered_list_prefix,
    verbalize_inline_code,
    verbalize_remaining_symbols,
    verbalize_structured_text,
)


_WHITESPACE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:!?])")
_DUPLICATE_SENTENCE_PERIOD = re.compile(r"(?<!\.)\.\.(?!\.)")
_BOUNDARY_PERIOD_ARTIFACT = re.compile(r"(?<!\.)([!?…:;])\.(?=\s|$)")
_PARENTHETICAL_STAGE_DIRECTION = re.compile(
    r"(?i)\(\s*(?:wzdycha|westchnienie|śmieje\s+się|śmiech|chichocze|"
    r"pauza|milczy|szeptem|krzyczy|ziewa|kaszle)\s*\)"
)
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U0001F3FB-\U0001F3FF"
    "\u200D\u20E3\uFE0E\uFE0F"
    "]+"
)
# This is linguistic normalization for standard Polish abbreviations, not a
# pronunciation dictionary for brands or product names. It runs on prose after
# Markdown parsing, so visible assistant text remains unchanged and URLs/code do
# not pass through this layer.
_POLISH_ABBREVIATIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(?<!\w)m\.\s*in\.(?=\s|[,;:!?…)\]}]|$)"), "między innymi"),
    (re.compile(r"(?i)(?<!\w)np\.(?=\s|[,;:!?…)\]}]|$)"), "na przykład"),
    (re.compile(r"(?i)(?<!\w)tzn\.(?=\s|[,;:!?…)\]}]|$)"), "to znaczy"),
    (re.compile(r"(?i)(?<!\w)tj\.(?=\s|[,;:!?…)\]}]|$)"), "to jest"),
    (re.compile(r"(?i)(?<!\w)itp\.(?=\s|[,;:!?…)\]}]|$)"), "i tym podobne"),
    (re.compile(r"(?i)(?<!\w)itd\.(?=\s|[,;:!?…)\]}]|$)"), "i tak dalej"),
    (re.compile(r"(?i)(?<!\w)prof\.(?=\s|[,;:!?…)\]}]|$)"), "profesor"),
    (re.compile(r"(?i)(?<!\w)dr\.?(?=\s|[,;:!?…)\]}]|$)"), "doktor"),
    (re.compile(r"(?i)(?<!\w)nr\.?(?=\s|[,;:!?…)\]}]|$)"), "numer"),
)


class MarkdownSpeechRenderer:
    """Render Markdown as natural text before it reaches a speech model.

    Display Markdown remains untouched. Code and raw HTML are intentionally not
    spoken; link labels are spoken without their target URL. Callers may render
    a complete document or use ``StreamingMarkdownSpeechSegmenter`` to delay
    incomplete Markdown constructs safely.
    """

    def __init__(self, language: str = "pl") -> None:
        self.language = conversation_language(language).code
        self._parser = MarkdownIt("commonmark", {"html": False}).enable("table")

    def render(self, markdown: str, *, finalize_document: bool = True) -> str:
        blocks: list[str] = []
        tokens = self._parser.parse(markdown)
        list_stack: list[dict[str, object]] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.type == "ordered_list_open":
                list_stack.append({
                    "ordered": True,
                    "next": int(token.attrGet("start") or 1),
                    "pending": None,
                })
                index += 1
                continue
            if token.type == "bullet_list_open":
                list_stack.append({"ordered": False, "next": 0, "pending": None})
                index += 1
                continue
            if token.type in {"ordered_list_close", "bullet_list_close"}:
                if list_stack:
                    list_stack.pop()
                index += 1
                continue
            if token.type == "list_item_open" and list_stack:
                context = list_stack[-1]
                if context["ordered"]:
                    number = int(context["next"])
                    context["next"] = number + 1
                    context["pending"] = ordered_list_prefix(number, self.language)
                index += 1
                continue
            if token.type == "list_item_close" and list_stack:
                list_stack[-1]["pending"] = None
                index += 1
                continue
            if token.type == "table_open":
                table_blocks, index = self._render_table(tokens, index + 1)
                blocks.extend(table_blocks)
                continue
            if token.type != "inline" or not token.children:
                index += 1
                continue
            text = self._render_inline(token.children)
            for context in reversed(list_stack):
                prefix = context.get("pending")
                if prefix:
                    text = f"{prefix}, {text}"
                    context["pending"] = None
                    break
            text = self._normalize(text)
            if text:
                blocks.append(text)
            index += 1
        return self._join_blocks(blocks, finalize_document=finalize_document)

    def _render_table(
        self, tokens: list[Token], start: int
    ) -> tuple[list[str], int]:
        """Turn a visual table into complete, self-contained spoken rows.

        Reading a table as a flat sequence of inline cells makes the column
        headers audible but leaves the data without context.  More importantly,
        that flat rendering changes its already-produced suffix whenever another
        streamed row arrives.  Each data row is therefore rendered atomically as
        ``Header: value`` pairs and ends in stable punctuation.
        """

        headers: list[str] = []
        rows: list[list[str]] = []
        current_row: list[str] | None = None
        in_header = False
        index = start
        while index < len(tokens):
            token = tokens[index]
            if token.type == "table_close":
                index += 1
                break
            if token.type == "thead_open":
                in_header = True
            elif token.type == "thead_close":
                in_header = False
            elif token.type == "tr_open":
                current_row = []
            elif token.type == "tr_close" and current_row is not None:
                if in_header and not headers:
                    headers = current_row
                else:
                    rows.append(current_row)
                current_row = None
            elif (
                token.type == "inline"
                and token.children
                and current_row is not None
            ):
                current_row.append(
                    self._normalize(self._render_inline(token.children))
                )
            index += 1

        spoken_rows: list[str] = []
        for row in rows:
            fields: list[str] = []
            for column, value in enumerate(row):
                if not value:
                    continue
                header = headers[column] if column < len(headers) else ""
                fields.append(f"{header}: {value}" if header else value)
            if fields:
                spoken_rows.append(self._join_blocks(fields))

        if not spoken_rows and headers:
            spoken_rows.append(
                self._ensure_terminal(
                    ("Columns: " if self.language == "en" else "Kolumny: ")
                    + ", ".join(headers)
                )
            )
        return spoken_rows, index

    @staticmethod
    def _ensure_terminal(text: str) -> str:
        text = text.strip()
        if text and text[-1] not in ".!?…:;":
            return text + "."
        return text

    def _render_inline(self, tokens: list[Token]) -> str:
        parts: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.type == "link_open":
                closing = self._matching_close(tokens, index, "link_open", "link_close")
                label = self._render_inline(tokens[index + 1 : closing])
                href = token.attrGet("href") or ""
                parts.append(self._link_label(label, href))
                index = closing + 1
                continue
            if token.type == "image":
                # Images belong to the visual answer. Reading alt text or a URL
                # without context is usually confusing in a voice conversation.
                index += 1
                continue
            if token.type == "text":
                # CommonMark already removed actual emphasis delimiters.  An
                # underscore still present here belongs to an identifier and
                # must become a word boundary, never be silently deleted.
                parts.append(token.content)
            elif token.type == "code_inline":
                parts.append(verbalize_inline_code(token.content, self.language))
            elif token.type in {"softbreak", "hardbreak"}:
                parts.append(". ")
            index += 1
        return "".join(parts)

    @staticmethod
    def _matching_close(
        tokens: list[Token], start: int, opening: str, closing: str
    ) -> int:
        depth = 0
        for index in range(start, len(tokens)):
            if tokens[index].type == opening:
                depth += 1
            elif tokens[index].type == closing:
                depth -= 1
                if depth == 0:
                    return index
        return start

    @staticmethod
    def _link_label(label: str, href: str) -> str:
        clean_label = label.strip()
        if clean_label and clean_label != href:
            return clean_label
        host = urlsplit(href).hostname
        return (host or "").removeprefix("www.")

    def _normalize(self, text: str) -> str:
        text = text.replace("|", ", ")
        text = _WHITESPACE.sub(" ", text).strip()
        if self.language == "pl":
            for pattern, replacement in _POLISH_ABBREVIATIONS:
                text = pattern.sub(replacement, text)
        # Parentheses can be interpreted by expressive TTS models as stage
        # directions. Drop explicit non-verbal cues and turn ordinary asides
        # into comma-delimited prose instead of sending control-like syntax.
        text = _PARENTHETICAL_STAGE_DIRECTION.sub(" ", text)
        text = text.translate(str.maketrans({"(": ", ", ")": ", ", "[": ", ", "]": ", "}))
        # Emoji are visual sentiment, not words. Higgs may vocalize them as
        # sighs, laughs or other non-verbal audio, so they never reach TTS.
        text = _EMOJI.sub(" ", text)
        # Structured spans and all standalone numbers are rendered explicitly
        # in the active conversation language.  Higgs therefore never needs to
        # guess how an isolated digit, slash or underscore should be spoken.
        text = verbalize_structured_text(text, self.language)
        text = verbalize_remaining_symbols(text, self.language)
        # Remove only malformed Markdown residue left as literal text.  The
        # underscore is intentionally absent: identifiers have already been
        # handled semantically above.
        text = text.translate(str.maketrans({"*": "", "~": ""}))
        text = _WHITESPACE.sub(" ", text).strip()
        # A Markdown hard break is rendered as a sentence boundary. When the
        # preceding list item already ends in a period this produces exactly
        # two periods; collapse that artifact while preserving a deliberate
        # three-dot ellipsis and the single-character ellipsis.
        text = _DUPLICATE_SENTENCE_PERIOD.sub(".", text)
        text = _BOUNDARY_PERIOD_ARTIFACT.sub(r"\1", text)
        text = re.sub(r",\s*,", ",", text)
        text = re.sub(r",\s*([.!?…:;])", r"\1", text)
        return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", text).strip(" ,")

    @staticmethod
    def _join_blocks(blocks: list[str], *, finalize_document: bool = True) -> str:
        result = ""
        for block in blocks:
            if result and result[-1] not in ".!?…:;":
                result += "."
            if result:
                result += " "
            result += block
        result = result.strip()
        if finalize_document and result and result[-1] not in ".!?…:;":
            result += "."
        return result
