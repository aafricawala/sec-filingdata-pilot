"""
test_sec_filing_extractors_live.py
Live integration tests for deterministic SEC filing section extraction.

These tests retrieve real SEC EDGAR filing documents through the existing
SEC-3/SEC-4 production clients and validate SEC-5.3 extraction behavior.

Requirements:
- Valid SEC User-Agent identity.
- Internet connectivity.
- SEC EDGAR availability.

These tests are intentionally separate from the offline mock tests.
"""

from __future__ import annotations

from sec_client import SECClient
from sec_filing_extractors import (
    _extract_heading_candidates_html,
    _taxonomy_key,
    extract_sections,
)
from sec_filings import SECFilingDocument, SECFilingsClient
from sec_submissions import SECFiling, SubmissionsClient


SEC_NAME = "Schwab Market Data Pilot"
SEC_EMAIL = "YOUR_EMAIL@example.com"
SEC_ORGANIZATION = "Schwab Market Data Pilot"

# MSFT is used only as a real validation sample.
# No MSFT-specific production behavior is exercised or assumed.
TEST_TICKER = "MSFT"

SUPPORTED_FORMS = {
    "10-K",
    "10-Q",
    "8-K",
    "10-K/A",
    "10-Q/A",
    "8-K/A",
}

def test_live_10q_heading_raw_structure_diagnostic() -> None:
    """Inspect raw HTML surrounding the first real 10-Q heading candidate."""
    (
        client,
        submissions_client,
        filings_client,
    ) = _get_live_clients()

    try:
        filings = _find_current_supported_filings(
            submissions_client
        )

        filing = next(
            filing
            for filing in filings
            if filing.form == "10-Q"
        )

        document = filings_client.get_primary_document(
            filing
        )

        candidates = _extract_heading_candidates_html(
            document.content,
            "10-Q",
        )

        assert candidates

        candidate = candidates[0]

        context_start = max(
            0,
            candidate.start_offset - 1500,
        )
        context_end = min(
            len(document.content),
            candidate.end_offset + 1500,
        )

        raw_context = document.content[
            context_start:context_end
        ]

        print("\nSEC-5.3 REAL 10-Q RAW HEADING STRUCTURE")
        print(
            f"ITEM={candidate.item_number!r} "
            f"TITLE={candidate.title!r}"
        )
        print(
            f"CANDIDATE_START={candidate.start_offset} "
            f"CANDIDATE_END={candidate.end_offset}"
        )
        print(
            raw_context.decode(
                "utf-8",
                errors="replace",
            )
        )

    finally:
        client.close()

def _get_live_clients() -> tuple[
    SECClient,
    SubmissionsClient,
    SECFilingsClient,
]:
    """Create the shared production SEC clients for live validation."""
    client = SECClient(
        name=SEC_NAME,
        email=SEC_EMAIL,
        organization=SEC_ORGANIZATION,
    )

    return (
        client,
        SubmissionsClient(client),
        SECFilingsClient(client),
    )


def _find_current_supported_filings(
    submissions_client: SubmissionsClient,
) -> list[SECFiling]:
    """Return currently available supported filings with primary documents."""
    filings = submissions_client.get_recent_filings_by_ticker(
        TEST_TICKER
    )

    assert filings, (
        f"No recent SEC filings were returned for {TEST_TICKER}."
    )

    assert all(
        isinstance(filing, SECFiling)
        for filing in filings
    )

    supported = [
        filing
        for filing in filings
        if (
            filing.form in SUPPORTED_FORMS
            and filing.primary_document
        )
    ]

    assert supported, (
        f"No supported filing with a primary document was found "
        f"for {TEST_TICKER}."
    )

    return supported


def _assert_extraction_contract(
    document: SECFilingDocument,
) -> tuple:
    """Validate the core SEC-5 section extraction contract."""
    sections = extract_sections(document)

    assert isinstance(sections, tuple)

    previous_start = -1

    for section in sections:
        assert section.filing is document.filing
        assert section.document is document

        assert section.start_offset >= 0
        assert section.start_offset < section.end_offset
        assert section.end_offset <= len(document.content)

        assert section.start_offset >= previous_start
        previous_start = section.start_offset

        assert section.content == document.content[
            section.start_offset : section.end_offset
        ]

        assert section.section_id
        assert section.section_title
        assert section.section_level >= 1
        assert section.occurrence >= 1

    return sections


def _is_inline_xbrl(document: SECFilingDocument) -> bool:
    """Detect inline-XBRL markup without interpreting filing data."""
    content_lower = document.content.lower()

    return (
        b"<ix:" in content_lower
        or b"</ix:" in content_lower
    )


def test_live_current_supported_filing_extraction() -> None:
    """Extract sections from a real current SEC primary filing."""
    (
        client,
        submissions_client,
        filings_client,
    ) = _get_live_clients()

    try:
        filings = _find_current_supported_filings(
            submissions_client
        )

        filing = filings[0]

        document = filings_client.get_primary_document(
            filing
        )

        assert isinstance(
            document,
            SECFilingDocument,
        )
        assert document.filing is filing
        assert document.document_kind == "primary"
        assert document.document_name == filing.primary_document
        assert document.content
        assert isinstance(document.content, bytes)
        assert document.content_hash

        _assert_extraction_contract(document)

    finally:
        client.close()


def test_live_supported_form_extraction_when_available() -> None:
    """Validate extraction for each distinct supported form currently available."""
    (
        client,
        submissions_client,
        filings_client,
    ) = _get_live_clients()

    try:
        filings = _find_current_supported_filings(
            submissions_client
        )

        forms_seen: set[str] = set()

        for filing in filings:
            if filing.form in forms_seen:
                continue

            forms_seen.add(filing.form)

            document = filings_client.get_primary_document(
                filing
            )

            assert isinstance(
                document,
                SECFilingDocument,
            )
            assert document.filing is filing
            assert document.content

            _assert_extraction_contract(document)

    finally:
        client.close()


def test_live_corpus_discovery_diagnostic() -> None:
    """Report the real SEC corpus available for SEC-5.3 validation."""
    (
        client,
        submissions_client,
        filings_client,
    ) = _get_live_clients()

    try:
        filings = _find_current_supported_filings(
            submissions_client
        )

        seen_forms: set[str] = set()
        seen_amendments: set[str] = set()
        seen_inline_xbrl: set[str] = set()

        print("\nSEC-5.3 LIVE CORPUS")

        for filing in filings:
            if filing.form in seen_forms:
                continue

            seen_forms.add(filing.form)

            document = filings_client.get_primary_document(
                filing
            )
            sections = _assert_extraction_contract(document)

            if filing.is_amendment:
                seen_amendments.add(filing.form)

            if _is_inline_xbrl(document):
                seen_inline_xbrl.add(filing.form)

            section_ids = [
                section.section_id
                for section in sections
            ]

            print(
                f"FORM={filing.form} "
                f"ACCESSION={filing.accession_number} "
                f"FILING_DATE={filing.filing_date} "
                f"REPORT_DATE={filing.report_date} "
                f"PRIMARY_DOCUMENT={filing.primary_document} "
                f"CONTENT_TYPE={document.content_type} "
                f"BYTES={len(document.content)} "
                f"SECTIONS={len(sections)} "
                f"AMENDED={filing.is_amendment} "
                f"INLINE_XBRL={_is_inline_xbrl(document)}"
            )
            print(f"SECTION_IDS={section_ids}")

        print(f"FORMS_SEEN={sorted(seen_forms)}")
        print(f"AMENDED_FORMS_SEEN={sorted(seen_amendments)}")
        print(
            f"INLINE_XBRL_FORMS_SEEN={sorted(seen_inline_xbrl)}"
        )

        assert seen_forms

    finally:
        client.close()


def test_live_10q_heading_candidate_diagnostic() -> None:
    """Inspect real 10-Q HTML heading candidates before parser changes."""
    (
        client,
        submissions_client,
        filings_client,
    ) = _get_live_clients()

    try:
        filings = _find_current_supported_filings(
            submissions_client
        )

        filing = next(
            filing
            for filing in filings
            if filing.form == "10-Q"
        )

        document = filings_client.get_primary_document(
            filing
        )

        candidates = _extract_heading_candidates_html(
            document.content,
            "10-Q",
        )

        print("\nSEC-5.3 REAL 10-Q HEADING CANDIDATES")

        for candidate in candidates:
            taxonomy_key = _taxonomy_key(
                "10-Q",
                candidate.item_number,
                candidate.title,
            )

            print(
                f"ITEM={candidate.item_number!r} "
                f"TITLE={candidate.title!r} "
                f"START={candidate.start_offset} "
                f"END={candidate.end_offset} "
                f"TAXONOMY={taxonomy_key!r}"
            )

        print(
            f"TOTAL_CANDIDATES={len(candidates)}"
        )

        assert candidates

    finally:
        client.close()