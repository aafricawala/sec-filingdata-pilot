from __future__ import annotations
import re
import html
from dataclasses import dataclass
from typing import Final, Optional
from models.sec_filings import SECFiling, SECFilingDocument
from extractors.sec_heading_models import _HeadingCandidate, _TaxonomyEntry, _get_base_form, _build_taxonomy, _taxonomy_key, _normalize_title
from extractors.sec_html_headings import _extract_heading_candidates_html
from extractors.sec_heading_models import _ITEM_HEADING_PATTERN

class SECFilingExtractionError(Exception):
    """Base exception for deterministic SEC filing extraction failures."""


class SECFilingFormatError(SECFilingExtractionError):
    """Raised when a filing document has an unsupported or malformed format."""


class SECFilingSectionError(SECFilingExtractionError):
    """Raised when a filing section contract or boundary is invalid."""


_SUPPORTED_FORMS: Final[frozenset[str]] = frozenset({"10-K", "10-Q", "8-K"})


@dataclass(frozen=True)
class SECFilingSection:
    """
    Immutable evidence object representing one deterministic filing section.

    Offsets use half-open semantics:

        [start_offset, end_offset)

    and refer directly to the original raw bytes.
    """

    filing: SECFiling
    document: SECFilingDocument
    section_id: str
    section_title: str
    section_level: int
    occurrence: int
    start_offset: int
    end_offset: int
    content: bytes

    def __post_init__(self) -> None:
        """Validate the immutable section evidence contract."""
        if not isinstance(self.filing, SECFiling):
            raise SECFilingSectionError("filing must be a SECFiling instance.")

        if not isinstance(self.document, SECFilingDocument):
            raise SECFilingSectionError(
                "document must be a SECFilingDocument instance."
            )

        if self.document.filing != self.filing:
            raise SECFilingSectionError(
                "document.filing must match the section filing."
            )

        if not isinstance(self.section_id, str) or not self.section_id.strip():
            raise SECFilingSectionError("section_id must be a non-empty string.")

        if not isinstance(self.section_title, str) or not self.section_title.strip():
            raise SECFilingSectionError(
                "section_title must be a non-empty string."
            )

        if not isinstance(self.section_level, int) or self.section_level < 1:
            raise SECFilingSectionError(
                "section_level must be a positive integer."
            )

        if not isinstance(self.occurrence, int) or self.occurrence < 1:
            raise SECFilingSectionError(
                "occurrence must be a positive integer."
            )

        if not isinstance(self.start_offset, int) or self.start_offset < 0:
            raise SECFilingSectionError(
                "start_offset must be a non-negative integer."
            )

        if not isinstance(self.end_offset, int) or self.end_offset < 0:
            raise SECFilingSectionError(
                "end_offset must be a non-negative integer."
            )

        if self.end_offset < self.start_offset:
            raise SECFilingSectionError(
                "end_offset must be greater than or equal to start_offset."
            )

        if not isinstance(self.content, bytes):
            raise SECFilingSectionError("content must be bytes.")

        if self.end_offset > len(self.document.content):
            raise SECFilingSectionError(
                "Section end_offset exceeds the document content length."
            )

        expected_content = self.document.content[
            self.start_offset:self.end_offset
        ]

        if self.content != expected_content:
            raise SECFilingSectionError(
                "content must exactly equal the corresponding raw document slice."
            )



def _validate_document(document: SECFilingDocument) -> str:
    """Validate the filing document and return its supported base form."""
    if not isinstance(document, SECFilingDocument):
        raise SECFilingFormatError(
            "document must be a SECFilingDocument instance."
        )

    base_form = _get_base_form(document.filing.form)

    if base_form not in _SUPPORTED_FORMS:
        raise SECFilingFormatError(
            f"Unsupported SEC filing form: {document.filing.form!r}."
        )

    if not isinstance(document.content, bytes):
        raise SECFilingFormatError("SECFilingDocument.content must be bytes.")

    if not document.content:
        raise SECFilingFormatError("Cannot extract sections from empty content.")

    return base_form


def _decode_plain_text(content: bytes) -> str:
    """Decode filing bytes for parsing while preserving raw bytes separately."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _line_start_offsets(content: bytes) -> tuple[int, ...]:
    """Return raw-byte offsets for the beginning of every text line."""
    offsets = [0]

    for match in re.finditer(b"\n", content):
        offsets.append(match.end())

    return tuple(offsets)


def _extract_heading_candidates(
    content: bytes,
    base_form: str,
) -> tuple[_HeadingCandidate, ...]:
    """Identify structurally plausible Item headings in plain-text content."""
    del base_form

    text = _decode_plain_text(content)
    line_offsets = _line_start_offsets(content)

    lines = text.splitlines(keepends=True)
    candidates: list[_HeadingCandidate] = []

    offset_index = 0

    for line in lines:
        line_without_ending = line.rstrip("\r\n")
        item_match = _ITEM_HEADING_PATTERN.match(line_without_ending)

        if item_match:
            number = item_match.group("number")
            letter = item_match.group("letter") or ""
            item_number = f"{number}{letter}".upper()
            title = item_match.group("title").strip()

            start_offset = line_offsets[offset_index]

            newline_offset = content.find(b"\n", start_offset)

            raw_line = content[
                start_offset:
                newline_offset + 1
                if newline_offset >= 0
                else len(content)
            ]

            end_offset = start_offset + len(raw_line)

            candidates.append(
                _HeadingCandidate(
                    item_number=item_number,
                    title=title,
                    start_offset=start_offset,
                    end_offset=end_offset,
                )
            )

        offset_index += 1

    return tuple(candidates)


def _extract_sections_from_candidates(
    document: SECFilingDocument,
    base_form: str,
    candidates: tuple[_HeadingCandidate, ...],
) -> tuple[SECFilingSection, ...]:
    """Convert recognized heading candidates into immutable evidence sections."""
    taxonomy = _build_taxonomy(base_form)

    recognized: list[tuple[_HeadingCandidate, _TaxonomyEntry]] = []

    for candidate in candidates:
        section_key = _taxonomy_key(
            base_form,
            candidate.item_number,
            candidate.title,
        )

        if section_key is None:
            continue

        entry = taxonomy.get(section_key)

        if entry is None:
            continue

        recognized.append((candidate, entry))

    recognized.sort(
        key=lambda pair: (
            pair[0].start_offset,
            pair[0].end_offset,
            pair[0].item_number,
        )
    )

    sections: list[SECFilingSection] = []
    occurrences: dict[str, int] = {}

    for index, (candidate, entry) in enumerate(recognized):
        next_start = (
            recognized[index + 1][0].start_offset
            if index + 1 < len(recognized)
            else len(document.content)
        )

        start_offset = candidate.start_offset
        end_offset = next_start

        if end_offset < start_offset:
            raise SECFilingSectionError(
                "Section boundaries are not monotonically ordered."
            )

        section_id = entry.section_id
        occurrences[section_id] = occurrences.get(section_id, 0) + 1

        sections.append(
            SECFilingSection(
                filing=document.filing,
                document=document,
                section_id=section_id,
                section_title=entry.title,
                section_level=entry.level,
                occurrence=occurrences[section_id],
                start_offset=start_offset,
                end_offset=end_offset,
                content=document.content[start_offset:end_offset],
            )
        )

    return tuple(sections)


def _extract_sections_plain_text(
    document: SECFilingDocument,
    base_form: str,
) -> tuple[SECFilingSection, ...]:
    """Extract recognized sections from a plain-text filing."""
    candidates = _extract_heading_candidates(
        document.content,
        base_form,
    )

    return _extract_sections_from_candidates(
        document,
        base_form,
        candidates,
    )


def _extract_sections_html(
    document: SECFilingDocument,
    base_form: str,
) -> tuple[SECFilingSection, ...]:
    """
    Extract recognized sections from HTML / inline-XBRL content.

    The raw scanner establishes source boundaries. HTML structure is used only
    to identify likely heading containers and reconstruct their text.
    """
    candidates = _extract_heading_candidates_html(
        document.content,
        base_form,
    )

    return _extract_sections_from_candidates(
        document,
        base_form,
        candidates,
    )


def extract_sections(
    document: SECFilingDocument,
) -> tuple[SECFilingSection, ...]:
    """
    Deterministically extract supported SEC filing sections.

    Plain-text and HTML/XML SEC filing representations are supported.

    For HTML/XML:
        - raw bytes remain authoritative;
        - structural interpretation is performed from raw tokens;
        - entity decoding is parsing-only;
        - section offsets always reference original raw bytes.

    Missing recognized sections are normal and return no section for that
    taxonomy entry.

    Returns:
        Immutable SECFilingSection objects ordered by source offset.

    Raises:
        SECFilingFormatError:
            If the document is invalid, empty, unsupported, or malformed.
    """
    base_form = _validate_document(document)
    content_type = document.content_type.strip().lower()

    if "html" in content_type or "xml" in content_type:
        return _extract_sections_html(document, base_form)

    return _extract_sections_plain_text(document, base_form)