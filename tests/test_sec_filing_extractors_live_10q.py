"""
test_sec_filing_extractors_live_10q.py
Live integration tests for deterministic SEC 10-Q section extraction.

Validates:
- real SEC 10-Q retrieval
- expected section taxonomy
- exact raw-byte section boundaries
- non-empty section content
- monotonic, non-overlapping offsets
- preservation of the source document bytes
"""

from __future__ import annotations

from sec_client import SECClient
from sec_filing_extractors import extract_sections
from sec_filings import SECFilingDocument, SECFilingsClient
from sec_submissions import SECFiling, SubmissionsClient


SEC_NAME = "Schwab Market Data Pilot"
SEC_EMAIL = "YOUR_EMAIL@example.com"
SEC_ORGANIZATION = "Schwab Market Data Pilot"

TEST_TICKER = "MSFT"

EXPECTED_10Q_SECTION_IDS = {
    "PART_I_ITEM_1",
    "PART_I_ITEM_2",
    "PART_I_ITEM_3",
    "PART_I_ITEM_4",
    "PART_II_ITEM_1",
    "PART_II_ITEM_1A",
    "PART_II_ITEM_2",
    "PART_II_ITEM_5",
    "PART_II_ITEM_6",
}


def _get_live_clients() -> tuple[
    SECClient,
    SubmissionsClient,
    SECFilingsClient,
]:
    """Create the production SEC clients for live validation."""
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


def _find_current_10q(
    submissions_client: SubmissionsClient,
) -> SECFiling:
    """Return the first currently available MSFT 10-Q with a primary document."""
    filings = submissions_client.get_recent_filings_by_ticker(
        TEST_TICKER
    )

    candidates = [
        filing
        for filing in filings
        if (
            filing.form == "10-Q"
            and filing.primary_document
        )
    ]

    assert candidates, (
        f"No current 10-Q with a primary document was found "
        f"for {TEST_TICKER}."
    )

    return candidates[0]


def test_live_10q_extracted_section_boundaries_and_content() -> None:
    """
    Validate actual extracted section boundaries and content for a real 10-Q.
    """
    (
        client,
        submissions_client,
        filings_client,
    ) = _get_live_clients()

    try:
        filing = _find_current_10q(
            submissions_client
        )

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

        sections = extract_sections(document)

        assert isinstance(sections, tuple)
        assert sections

        section_ids = [
            section.section_id
            for section in sections
        ]

        assert set(section_ids) == EXPECTED_10Q_SECTION_IDS
        assert len(section_ids) == len(
            EXPECTED_10Q_SECTION_IDS
        )

        previous_end = 0

        for section in sections:
            assert section.filing is filing
            assert section.document is document

            assert section.start_offset >= previous_end
            assert section.start_offset < section.end_offset
            assert section.end_offset <= len(
                document.content
            )

            exact_source_slice = document.content[
                section.start_offset : section.end_offset
            ]

            assert section.content == exact_source_slice
            assert section.content

            assert section.section_id
            assert section.section_title
            assert section.section_level >= 1
            assert section.occurrence >= 1

            previous_end = section.end_offset

            print(
                f"SECTION={section.section_id} "
                f"TITLE={section.section_title!r} "
                f"START={section.start_offset} "
                f"END={section.end_offset} "
                f"BYTES={len(section.content)}"
            )

        print("\nSEC-5.3 REAL 10-Q EXTRACTION VALIDATION")
        print(
            f"FORM={filing.form} "
            f"ACCESSION={filing.accession_number} "
            f"DOCUMENT={filing.primary_document}"
        )
        print(
            f"DOCUMENT_BYTES={len(document.content)} "
            f"SECTIONS={len(sections)}"
        )
        print(f"SECTION_IDS={section_ids}")

    finally:
        client.close()
def test_live_8k_amendment_extraction_contract() -> None:
    """Validate extraction contract for a real amended 8-K filing."""
    (
        client,
        submissions_client,
        filings_client,
    ) = _get_live_clients()

    try:
        filings = submissions_client.get_recent_filings_by_ticker(
            TEST_TICKER
        )

        amended_filings = [
            filing
            for filing in filings
            if (
                filing.form == "8-K/A"
                and filing.is_amendment
                and filing.primary_document
            )
        ]

        assert amended_filings, (
            f"No current 8-K/A with a primary document was found "
            f"for {TEST_TICKER}."
        )

        filing = amended_filings[0]

        document = filings_client.get_primary_document(
            filing
        )

        assert isinstance(document, SECFilingDocument)
        assert document.filing is filing
        assert document.document_kind == "primary"
        assert document.document_name == filing.primary_document
        assert document.content

        sections = extract_sections(document)

        assert isinstance(sections, tuple)

        previous_end = 0

        for section in sections:
            assert section.filing is filing
            assert section.document is document
            assert section.start_offset >= previous_end
            assert section.start_offset < section.end_offset
            assert section.end_offset <= len(document.content)

            assert section.content == document.content[
                section.start_offset : section.end_offset
            ]

            assert section.content

            previous_end = section.end_offset

        print("\nSEC-5.3 REAL 8-K/A EXTRACTION VALIDATION")
        print(
            f"FORM={filing.form} "
            f"ACCESSION={filing.accession_number} "
            f"AMENDED={filing.is_amendment} "
            f"DOCUMENT={filing.primary_document}"
        )
        print(
            f"DOCUMENT_BYTES={len(document.content)} "
            f"SECTIONS={len(sections)}"
        )
        print(
            f"SECTION_IDS={[section.section_id for section in sections]}"
        )

    finally:
        client.close()