"""Offline unit tests for deterministic SEC filing section extraction.

Coverage:
- SEC-5.1 plain-text section extraction contract.
- SEC-5.2 raw HTML byte scanner.
- SEC-5.2 deterministic HTML structural extraction.
- SEC-5.3 regression coverage for decimal 8-K Item numbers.

No SEC network access is used.
"""

from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest

from sec_filing_extractors import (
    SECFilingExtractionError,
    SECFilingFormatError,
    _scan_raw_html,
    extract_sections,
)
from sec_filings import SECFilingDocument
from sec_submissions import SECFiling


def _make_filing(
    *,
    form: str = "10-K",
    cik: int = 789019,
    accession_number: str = "0000789019-25-000001",
    filing_date: str = "2025-01-30",
    report_date: str = "2024-12-31",
    primary_document: str = "msft-20241231.htm",
    is_amendment: bool = False,
    file_number: str = "001-37845",
) -> SECFiling:
    """Create deterministic filing metadata for unit tests."""
    return SECFiling(
        cik=cik,
        form=form,
        accession_number=accession_number,
        filing_date=filing_date,
        report_date=report_date,
        primary_document=primary_document,
        is_amendment=is_amendment,
        file_number=file_number,
        filing_url=(
            "https://www.sec.gov/Archives/edgar/data/"
            "789019/000078901925000001/msft-20241231.htm"
        ),
    )


def _make_document(
    content: bytes,
    *,
    content_type: str = "text/plain",
    form: str = "10-K",
    primary_document: str = "msft-20241231.htm",
) -> SECFilingDocument:
    """Create a deterministic immutable filing document."""
    filing = _make_filing(
        form=form,
        primary_document=primary_document,
        is_amendment=form.endswith("/A"),
    )

    return SECFilingDocument(
        filing=filing,
        document_name=primary_document,
        document_kind="primary",
        source_url=filing.filing_url,
        content_type=content_type,
        content_hash=sha256(content).hexdigest(),
        content=content,
    )


# ---------------------------------------------------------------------------
# SEC-5.1 plain-text extraction tests
# ---------------------------------------------------------------------------


def test_section_is_immutable() -> None:
    """Extracted sections must be immutable value objects."""
    document = _make_document(
        b"Item 1. Business\n"
        b"Business content.\n"
        b"Item 1A. Risk Factors\n"
        b"Risk content.\n"
    )

    sections = extract_sections(document)

    assert sections

    with pytest.raises(FrozenInstanceError):
        sections[0].section_title = "Modified"  # type: ignore[misc]


def test_section_content_is_exact_raw_slice() -> None:
    """Section content must equal the exact source-byte slice."""
    content = (
        b"Item 1. Business\n"
        b"Business content.\n"
        b"Item 1A. Risk Factors\n"
        b"Risk content.\n"
    )
    document = _make_document(content)

    sections = extract_sections(document)

    assert sections

    for section in sections:
        assert section.content == content[
            section.start_offset:section.end_offset
        ]


def test_section_offsets_are_half_open() -> None:
    """Section offsets must use the [start_offset, end_offset) convention."""
    content = b"Item 1. Business\nBusiness content.\n"
    document = _make_document(content)

    sections = extract_sections(document)

    assert len(sections) == 1

    section = sections[0]

    assert 0 <= section.start_offset < section.end_offset <= len(content)
    assert section.content == content[
        section.start_offset:section.end_offset
    ]


def test_sections_are_deterministically_ordered() -> None:
    """Sections must be returned in ascending source-offset order."""
    content = (
        b"Item 1A. Risk Factors\n"
        b"Risk content.\n"
        b"Item 7. Management's Discussion and Analysis\n"
        b"MD&A content.\n"
        b"Item 8. Financial Statements\n"
        b"Financial statement content.\n"
    )
    document = _make_document(content)

    sections = extract_sections(document)

    assert [section.start_offset for section in sections] == sorted(
        section.start_offset for section in sections
    )


def test_missing_sections_are_tolerated() -> None:
    """Missing target sections must not cause extraction to fail."""
    content = (
        b"Item 1. Business\n"
        b"Business content only.\n"
    )
    document = _make_document(content)

    sections = extract_sections(document)

    assert len(sections) == 1
    assert sections[0].section_id == "ITEM_1"
    assert sections[0].section_title == "Business"


def test_duplicate_headings_preserve_occurrence_index() -> None:
    """Repeated target headings must preserve occurrence numbers."""
    content = (
        b"Item 1. Business\n"
        b"First business section.\n"
        b"Item 1. Business\n"
        b"Second business section.\n"
    )
    document = _make_document(content)

    sections = extract_sections(document)

    assert len(sections) == 2
    assert [section.occurrence for section in sections] == [1, 2]
    assert sections[0].content != sections[1].content


def test_amended_form_is_supported() -> None:
    """10-K/A must use the same base taxonomy as 10-K."""
    content = (
        b"Item 1. Business\n"
        b"Business content.\n"
        b"Item 1A. Risk Factors\n"
        b"Risk content.\n"
    )
    document = _make_document(
        content,
        form="10-K/A",
    )

    sections = extract_sections(document)

    assert [section.section_id for section in sections] == [
        "ITEM_1",
        "ITEM_1A",
    ]
    assert [section.section_title for section in sections] == [
        "Business",
        "Risk Factors",
    ]


def test_unsupported_form_is_rejected() -> None:
    """Unsupported filing forms must fail explicitly."""
    document = _make_document(
        b"Item 1. Business\nBusiness content.\n",
        form="20-F",
    )

    with pytest.raises(
        SECFilingFormatError,
        match="Unsupported SEC filing form",
    ):
        extract_sections(document)


def test_invalid_document_object_is_rejected() -> None:
    """The public extractor must reject invalid document objects."""
    with pytest.raises(SECFilingExtractionError):
        extract_sections(object())  # type: ignore[arg-type]


def test_empty_content_is_rejected() -> None:
    """Empty filing content cannot produce section boundaries."""
    document = _make_document(b"")

    with pytest.raises(SECFilingFormatError, match="empty"):
        extract_sections(document)


def test_repeated_extraction_is_deterministic() -> None:
    """Identical input must produce identical extraction results."""
    content = (
        b"Item 1. Business\n"
        b"Business content.\n"
        b"Item 1A. Risk Factors\n"
        b"Risk content.\n"
        b"Item 7. Management's Discussion and Analysis\n"
        b"MD&A content.\n"
    )
    document = _make_document(content)

    first = extract_sections(document)
    second = extract_sections(document)

    assert first == second


def test_incorrect_raw_slice_is_detectable() -> None:
    """Section content must remain tied to its declared source offsets."""
    content = b"Item 1. Business\nBusiness content.\n"
    document = _make_document(content)

    sections = extract_sections(document)

    assert len(sections) == 1

    section = sections[0]

    expected = content[section.start_offset:section.end_offset]

    assert section.content == expected
    assert section.content != content[
        section.start_offset + 1:section.end_offset
    ]


# ---------------------------------------------------------------------------
# SEC-5.2 raw HTML scanner tests
# ---------------------------------------------------------------------------


def test_raw_html_scanner_preserves_exact_byte_offsets() -> None:
    """The raw scanner must identify tags using original byte offsets."""
    content = (
        b"<html><body>"
        b"<h1>Item 1. Business</h1>"
        b"<p>Example content.</p>"
        b"</body></html>"
    )

    tokens = _scan_raw_html(content)

    h1_tokens = [
        token
        for token in tokens
        if token.tag_name == b"h1"
    ]

    assert len(h1_tokens) == 2

    start_token, end_token = h1_tokens

    assert start_token.token_type == "start_tag"
    assert end_token.token_type == "end_tag"

    assert content[
        start_token.start_offset:start_token.end_offset
    ] == b"<h1>"

    assert content[
        end_token.start_offset:end_token.end_offset
    ] == b"</h1>"


def test_raw_html_scanner_identifies_nested_inline_markup() -> None:
    """Nested tags must be tokenized independently."""
    content = (
        b"<h1>Item 1. "
        b"<span>Business</span>"
        b"</h1>"
    )

    tokens = _scan_raw_html(content)

    tag_names = [
        token.tag_name
        for token in tokens
        if token.tag_name is not None
    ]

    assert tag_names == [b"h1", b"span", b"span", b"h1"]

    span_start = next(
        token
        for token in tokens
        if token.tag_name == b"span"
        and token.token_type == "start_tag"
    )

    span_end = next(
        token
        for token in tokens
        if token.tag_name == b"span"
        and token.token_type == "end_tag"
    )

    assert content[
        span_start.start_offset:span_start.end_offset
    ] == b"<span>"

    assert content[
        span_end.start_offset:span_end.end_offset
    ] == b"</span>"


def test_raw_html_scanner_handles_gt_inside_quoted_attribute() -> None:
    """A > inside a quoted attribute must not terminate the tag."""
    content = (
        b'<div data-value="a > b">'
        b"Item 1. Business"
        b"</div>"
    )

    tokens = _scan_raw_html(content)

    div_start = next(
        token
        for token in tokens
        if token.tag_name == b"div"
        and token.token_type == "start_tag"
    )

    assert content[
        div_start.start_offset:div_start.end_offset
    ] == b'<div data-value="a > b">'


def test_raw_html_scanner_preserves_entities_as_raw_bytes() -> None:
    """HTML entities must remain unchanged in the raw source."""
    content = b"<p>Example &amp; test &#39;value&#39;.</p>"

    tokens = _scan_raw_html(content)

    paragraph_start = next(
        token
        for token in tokens
        if token.tag_name == b"p"
        and token.token_type == "start_tag"
    )

    paragraph_end = next(
        token
        for token in tokens
        if token.tag_name == b"p"
        and token.token_type == "end_tag"
    )

    assert content[
        paragraph_start.end_offset:paragraph_end.start_offset
    ] == b"Example &amp; test &#39;value&#39;."


def test_raw_html_scanner_handles_comments_and_declarations() -> None:
    """Comments/declarations must not corrupt later tag offsets."""
    content = (
        b"<!DOCTYPE html>"
        b"<html>"
        b"<!-- comment with > characters -->"
        b"<body>"
        b"<?processing instruction?>"
        b"<h1>Item 1. Business</h1>"
        b"</body>"
        b"</html>"
    )

    tokens = _scan_raw_html(content)

    h1_start = next(
        token
        for token in tokens
        if token.tag_name == b"h1"
        and token.token_type == "start_tag"
    )

    assert content[
        h1_start.start_offset:h1_start.end_offset
    ] == b"<h1>"

    assert content[h1_start.end_offset:].startswith(
        b"Item 1. Business</h1>"
    )


def test_raw_html_scanner_is_deterministic() -> None:
    """Identical HTML input must produce identical scanner output."""
    content = (
        b"<html><body>"
        b"<h1>Item 1. Business</h1>"
        b"<p>Example.</p>"
        b"</body></html>"
    )

    first = _scan_raw_html(content)
    second = _scan_raw_html(content)

    assert first == second


def test_raw_html_scanner_treats_invalid_less_than_as_text() -> None:
    """Invalid '<' text must not corrupt subsequent valid tags."""
    content = (
        b"<p>Value 1 < Value 2</p>"
        b"<h1>Item 1. Business</h1>"
    )

    tokens = _scan_raw_html(content)

    tag_names = [
        token.tag_name
        for token in tokens
        if token.tag_name is not None
    ]

    assert tag_names == [b"p", b"p", b"h1", b"h1"]


# ---------------------------------------------------------------------------
# SEC-5.2 / SEC-5.3 HTML extraction tests
# ---------------------------------------------------------------------------


def test_html_extraction_identifies_structural_sections() -> None:
    """HTML headings must produce recognized filing sections."""
    content = (
        b"<html><body>"
        b"<h1>Item 1. Business</h1>"
        b"<p>Business content.</p>"
        b"<h1>Item 1A. Risk Factors</h1>"
        b"<p>Risk content.</p>"
        b"<h1>Item 7. Management's Discussion and Analysis</h1>"
        b"<p>MD&amp;A content.</p>"
        b"<h1>Item 8. Financial Statements</h1>"
        b"<p>Financial content.</p>"
        b"</body></html>"
    )
    document = _make_document(
        content,
        content_type="text/html",
    )

    sections = extract_sections(document)

    section_ids = [section.section_id for section in sections]

    assert "ITEM_1" in section_ids
    assert "ITEM_1A" in section_ids
    assert "ITEM_7" in section_ids
    assert "ITEM_8" in section_ids


def test_html_extraction_preserves_exact_raw_section_bytes() -> None:
    """HTML section content must equal the corresponding raw byte slice."""
    content = (
        b"<html><body>\n"
        b"<div class=\"section\">"
        b"<h1>Item 1. Business</h1>"
        b"<p>Example &amp; test.</p>"
        b"</div>"
        b"<div>"
        b"<h1>Item 1A. Risk Factors</h1>"
        b"<p>Risk content.</p>"
        b"</div>"
        b"</body></html>"
    )
    document = _make_document(
        content,
        content_type="text/html",
    )

    sections = extract_sections(document)

    assert sections

    for section in sections:
        assert section.content == content[
            section.start_offset:section.end_offset
        ]


def test_html_extraction_handles_nested_inline_markup() -> None:
    """Heading recognition must tolerate inline markup."""
    content = (
        b"<html><body>"
        b"<h1>Item 1. <span>Business</span></h1>"
        b"<p>Business content.</p>"
        b"<h2>Item 1A. <b>Risk Factors</b></h2>"
        b"<p>Risk content.</p>"
        b"</body></html>"
    )
    document = _make_document(
        content,
        content_type="text/html",
    )

    sections = extract_sections(document)

    section_ids = [section.section_id for section in sections]

    assert "ITEM_1" in section_ids
    assert "ITEM_1A" in section_ids


def test_html_extraction_handles_entities_in_heading_text() -> None:
    """HTML entity decoding must not destroy raw-byte provenance."""
    content = (
        b"<html><body>"
        b"<h1>Item 1. Business &amp; Operations</h1>"
        b"<p>Business content.</p>"
        b"<h1>Item 1A. Risk Factors</h1>"
        b"<p>Risk content.</p>"
        b"</body></html>"
    )
    document = _make_document(
        content,
        content_type="text/html",
    )

    sections = extract_sections(document)

    assert sections

    assert any(
        section.section_id == "ITEM_1"
        for section in sections
    )

    for section in sections:
        assert section.content == content[
            section.start_offset:section.end_offset
        ]


def test_html_extraction_preserves_duplicate_heading_occurrences() -> None:
    """Repeated HTML headings must receive stable occurrence numbers."""
    content = (
        b"<html><body>"
        b"<h1>Item 1. Business</h1>"
        b"<p>First occurrence.</p>"
        b"<h2>Item 1. Business</h2>"
        b"<p>Second occurrence.</p>"
        b"</body></html>"
    )
    document = _make_document(
        content,
        content_type="text/html",
    )

    sections = extract_sections(document)

    business_sections = [
        section
        for section in sections
        if section.section_id == "ITEM_1"
    ]

    assert len(business_sections) == 2
    assert [section.occurrence for section in business_sections] == [1, 2]


def test_html_extraction_is_deterministic() -> None:
    """Repeated HTML extraction must return identical results."""
    content = (
        b"<html><body>"
        b"<h1>Item 1. Business</h1>"
        b"<p>Business content.</p>"
        b"<h1>Item 1A. Risk Factors</h1>"
        b"<p>Risk content.</p>"
        b"</body></html>"
    )
    document = _make_document(
        content,
        content_type="text/html",
    )

    first = extract_sections(document)
    second = extract_sections(document)

    assert first == second


def test_html_extraction_supports_amended_forms() -> None:
    """HTML extraction must support amended filing forms."""
    content = (
        b"<html><body>"
        b"<h1>Item 1. Business</h1>"
        b"<p>Amended business content.</p>"
        b"<h1>Item 1A. Risk Factors</h1>"
        b"<p>Amended risk content.</p>"
        b"</body></html>"
    )
    document = _make_document(
        content,
        content_type="text/html",
        form="10-K/A",
    )

    sections = extract_sections(document)

    section_ids = [section.section_id for section in sections]

    assert "ITEM_1" in section_ids
    assert "ITEM_1A" in section_ids


def test_html_extraction_supports_decimal_8k_item_numbers() -> None:
    """
    8-K headings use decimal item numbers such as 7.01 and 9.01.

    This reproduces the structure observed in the real MSFT 8-K that
    previously produced zero extracted sections.
    """
    content = (
        b"<html><body>"
        b"<div>"
        b"<p>Item 7.01. Regulation FD Disclosure</p>"
        b"<p>Regulation FD content.</p>"
        b"</div>"
        b"<div>"
        b"<p>Item 9.01. Financial Statements and Exhibits</p>"
        b"<p>Financial statements and exhibits content.</p>"
        b"</div>"
        b"</body></html>"
    )

    document = _make_document(
        content,
        content_type="text/html",
        form="8-K",
        primary_document="msft-8k.htm",
    )

    sections = extract_sections(document)

    assert [section.section_id for section in sections] == [
        "ITEM_7_01",
        "ITEM_9_01",
    ]

    assert [section.section_title for section in sections] == [
        "Regulation FD Disclosure",
        "Financial Statements and Exhibits",
    ]

    for section in sections:
        assert section.content == document.content[
            section.start_offset:section.end_offset
        ]


def test_html_extraction_supports_decimal_8k_item_numbers_with_unicode_space() -> None:
    """
    8-K headings may contain Unicode whitespace between Item and the decimal
    item number. This reproduces the formatting class observed in SEC HTML.
    """
    content = (
        "<html><body>"
        "<p>Item\u20097.01. Regulation FD Disclosure</p>"
        "<p>Regulation FD content.</p>"
        "<p>Item\u20099.01. Financial Statements and Exhibits</p>"
        "<p>Financial statements and exhibits content.</p>"
        "</body></html>"
    ).encode("utf-8")

    document = _make_document(
        content,
        content_type="text/html",
        form="8-K",
        primary_document="msft-8k.htm",
    )

    sections = extract_sections(document)

    assert [section.section_id for section in sections] == [
        "ITEM_7_01",
        "ITEM_9_01",
    ]

    for section in sections:
        assert section.content == document.content[
            section.start_offset:section.end_offset
        ]