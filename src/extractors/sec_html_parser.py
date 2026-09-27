from __future__ import annotations
from dataclasses import dataclass
import html
from typing import Final
from extractors.sec_html_scanner import _RawHTMLToken, _extract_raw_tag_name

@dataclass(frozen=True)
class _RawHTMLElement:
    """
    Internal representation of an HTML element reconstructed from raw tokens.

    Offsets always refer to the original document bytes.
    """

    tag_name: bytes
    start_offset: int
    end_offset: int
    child_indexes: tuple[int, ...]


def _is_self_closing_start_tag(
    content: bytes,
    token: _RawHTMLToken,
) -> bool:
    """Return whether a raw start tag ends with '/>'."""
    if token.token_type != "start_tag":
        return False

    tag_content = content[token.start_offset:token.end_offset].rstrip()

    return tag_content.endswith(b"/>") or token.tag_name in _HTML_VOID_ELEMENTS


def _build_raw_html_elements(
    content: bytes,
    tokens: tuple[_RawHTMLToken, ...],
) -> tuple[_RawHTMLElement, ...]:
    """
    Reconstruct a tolerant element tree from raw tokens.

    This is intentionally not a standards-complete HTML parser. Its purpose
    is limited to recovering source ranges and parent/child relationships
    needed for deterministic heading recognition.

    Malformed end tags are tolerated where possible because SEC filings may
    contain imperfect issuer-generated HTML.
    """
    elements: list[_RawHTMLElement] = []
    mutable_children: list[list[int]] = []
    stack: list[int] = []

    for token in tokens:
        if token.token_type == "start_tag":
            if token.tag_name is None:
                continue

            element_index = len(elements)

            elements.append(
                _RawHTMLElement(
                    tag_name=token.tag_name,
                    start_offset=token.start_offset,
                    end_offset=token.end_offset,
                    child_indexes=(),
                )
            )
            mutable_children.append([])

            if stack:
                mutable_children[stack[-1]].append(element_index)

            if not _is_self_closing_start_tag(content, token):
                stack.append(element_index)

        elif token.token_type == "end_tag":
            if token.tag_name is None:
                continue

            matching_position: Optional[int] = None

            for position in range(len(stack) - 1, -1, -1):
                if elements[stack[position]].tag_name == token.tag_name:
                    matching_position = position
                    break

            if matching_position is None:
                continue

            element_index = stack[matching_position]

            elements[element_index] = _RawHTMLElement(
                tag_name=elements[element_index].tag_name,
                start_offset=elements[element_index].start_offset,
                end_offset=token.end_offset,
                child_indexes=tuple(mutable_children[element_index]),
            )

            del stack[matching_position:]

    for element_index in stack:
        elements[element_index] = _RawHTMLElement(
            tag_name=elements[element_index].tag_name,
            start_offset=elements[element_index].start_offset,
            end_offset=len(content),
            child_indexes=tuple(mutable_children[element_index]),
        )

    return tuple(elements)


def _element_text(
    content: bytes,
    tokens: tuple[_RawHTMLToken, ...],
    element: _RawHTMLElement,
) -> str:
    """
    Recover human-readable text contained by an element.

    Adjacent raw text nodes are concatenated directly. This is important for
    SEC-generated HTML that splits one visible word across nested spans, e.g.
    'FINA' + 'NCIAL' -> 'FINANCIAL'.

    Entity decoding occurs only in this temporary structural representation.
    The original bytes remain untouched and authoritative.
    """
    pieces: list[str] = []

    for token in tokens:
        if token.start_offset < element.start_offset:
            continue

        if token.end_offset > element.end_offset:
            break

        if token.token_type != "text":
            continue

        raw_text = content[token.start_offset:token.end_offset]

        try:
            decoded = raw_text.decode("utf-8")
        except UnicodeDecodeError:
            decoded = raw_text.decode("latin-1")

        pieces.append(decoded)

    return html.unescape("".join(pieces))



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
