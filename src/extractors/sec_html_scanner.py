from __future__ import annotations
from dataclasses import dataclass
from typing import Final, Optional

import re

@dataclass(frozen=True)
class _RawHTMLToken:
    """
    Immutable representation of one raw HTML lexical token.

    Offsets are byte offsets into the original filing bytes.
    The end offset is exclusive.
    """

    token_type: str
    start_offset: int
    end_offset: int
    tag_name: Optional[bytes] = None


_HTML_TAG_NAME_CHARS = frozenset(
    b"abcdefghijklmnopqrstuvwxyz"
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    b"0123456789"
    b":_-"
)

_HTML_VOID_ELEMENTS: Final[frozenset[bytes]] = frozenset(
    {
        b"area",
        b"base",
        b"br",
        b"col",
        b"embed",
        b"hr",
        b"img",
        b"input",
        b"link",
        b"meta",
        b"param",
        b"source",
        b"track",
        b"wbr",
    }
)

_HTML_BLOCK_ELEMENTS: Final[frozenset[bytes]] = frozenset(
    {
        b"address",
        b"article",
        b"aside",
        b"blockquote",
        b"body",
        b"caption",
        b"dd",
        b"div",
        b"dl",
        b"dt",
        b"fieldset",
        b"figcaption",
        b"figure",
        b"footer",
        b"form",
        b"h1",
        b"h2",
        b"h3",
        b"h4",
        b"h5",
        b"h6",
        b"header",
        b"li",
        b"main",
        b"nav",
        b"ol",
        b"p",
        b"pre",
        b"section",
        b"table",
        b"tbody",
        b"td",
        b"tfoot",
        b"th",
        b"thead",
        b"tr",
        b"ul",
    }
)


def _is_html_tag_name_start(value: int) -> bool:
    """Return whether a byte can begin an HTML tag name."""
    return 65 <= value <= 90 or 97 <= value <= 122


def _is_html_tag_name_byte(value: int) -> bool:
    """Return whether a byte can occur inside an HTML tag name."""
    return value in _HTML_TAG_NAME_CHARS


def _find_tag_end(content: bytes, start_offset: int) -> int:
    """
    Find the exclusive end of an HTML tag while respecting quoted attributes.

    A '>' inside a quoted attribute does not terminate the tag.
    """
    quote: Optional[int] = None
    index = start_offset + 1
    length = len(content)

    while index < length:
        current = content[index]

        if quote is not None:
            if current == quote:
                quote = None
        elif current in (34, 39):
            quote = current
        elif current == 62:
            return index + 1

        index += 1

    raise ValueError(
        "Unterminated HTML tag encountered while scanning raw filing content."
    )


def _find_comment_end(content: bytes, start_offset: int) -> int:
    """Find the exclusive end of an HTML comment."""
    end_marker = content.find(b"-->", start_offset + 4)

    if end_marker < 0:
        raise ValueError(
            "Unterminated HTML comment encountered while scanning "
            "raw filing content."
        )

    return end_marker + 3


def _find_processing_instruction_end(
    content: bytes,
    start_offset: int,
) -> int:
    """Find the exclusive end of an XML processing instruction."""
    end_marker = content.find(b"?>", start_offset + 2)

    if end_marker < 0:
        raise ValueError(
            "Unterminated processing instruction encountered while "
            "scanning raw filing content."
        )

    return end_marker + 2


def _extract_raw_tag_name(
    content: bytes,
    start_offset: int,
    end_offset: int,
) -> Optional[bytes]:
    """Extract a lowercase ASCII tag name from a raw tag token."""
    index = start_offset + 1

    if index < end_offset and content[index] == 47:
        index += 1

    while index < end_offset and content[index] in b" \t\r\n\f":
        index += 1

    if index >= end_offset:
        return None

    if not _is_html_tag_name_start(content[index]):
        return None

    name_start = index
    index += 1

    while index < end_offset and _is_html_tag_name_byte(content[index]):
        index += 1

    return content[name_start:index].lower()


def _scan_raw_html(content: bytes) -> tuple[_RawHTMLToken, ...]:
    """
    Scan original HTML bytes into deterministic lexical tokens.

    The scanner never decodes or rewrites the source. Every offset therefore
    refers directly to the original raw byte sequence.
    """
    if not isinstance(content, bytes):
        raise ValueError(
            "Raw HTML scanner requires document content as bytes."
        )

    if not content:
        return ()

    tokens: list[_RawHTMLToken] = []
    length = len(content)
    text_start = 0
    index = 0

    while index < length:
        if content[index] != 60:
            index += 1
            continue

        if text_start < index:
            tokens.append(
                _RawHTMLToken(
                    token_type="text",
                    start_offset=text_start,
                    end_offset=index,
                )
            )

        if content.startswith(b"<!--", index):
            end_offset = _find_comment_end(content, index)
            tokens.append(
                _RawHTMLToken(
                    token_type="comment",
                    start_offset=index,
                    end_offset=end_offset,
                )
            )
            index = end_offset
            text_start = index
            continue

        if content.startswith(b"<!", index):
            end_offset = _find_tag_end(content, index)
            tokens.append(
                _RawHTMLToken(
                    token_type="declaration",
                    start_offset=index,
                    end_offset=end_offset,
                )
            )
            index = end_offset
            text_start = index
            continue

        if content.startswith(b"<?", index):
            end_offset = _find_processing_instruction_end(content, index)
            tokens.append(
                _RawHTMLToken(
                    token_type="processing_instruction",
                    start_offset=index,
                    end_offset=end_offset,
                )
            )
            index = end_offset
            text_start = index
            continue

        candidate_index = index + 1

        if candidate_index < length and content[candidate_index] == 47:
            candidate_index += 1

        if (
            candidate_index >= length
            or not _is_html_tag_name_start(content[candidate_index])
        ):
            index += 1
            continue

        end_offset = _find_tag_end(content, index)

        tag_name = _extract_raw_tag_name(
            content,
            index,
            end_offset,
        )

        if tag_name is None:
            index += 1
            continue

        is_end_tag = content[index + 1:index + 2] == b"/"

        tokens.append(
            _RawHTMLToken(
                token_type="end_tag" if is_end_tag else "start_tag",
                start_offset=index,
                end_offset=end_offset,
                tag_name=tag_name,
            )
        )

        index = end_offset
        text_start = index

    if text_start < length:
        tokens.append(
            _RawHTMLToken(
                token_type="text",
                start_offset=text_start,
                end_offset=length,
            )
        )

    return tuple(tokens)


# ---------------------------------------------------------------------------
# Raw HTML structural interpretation
# ---------------------------------------------------------------------------
