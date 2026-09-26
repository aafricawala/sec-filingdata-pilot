"""
test_sec_filings_live.py

Live integration tests for sec_filings.py.

These tests make real requests to SEC EDGAR.

Requirements:
- Valid SEC User-Agent identity.
- Internet connectivity.
- SEC EDGAR availability.

This file is intentionally separate from test_sec_filings_mock.py
so deterministic unit tests do not depend on external services.
"""

from __future__ import annotations

from sec_client import SECClient
from sec_filings import (
    SECFilingDocument,
    SECFilingsClient,
)
from sec_submissions import (
    SECFiling,
    SubmissionsClient,
)


SEC_NAME = "Schwab Market Data Pilot"
SEC_EMAIL = "YOUR_EMAIL@example.com"
SEC_ORGANIZATION = "Schwab Market Data Pilot"

TEST_TICKER = "MSFT"
EXPECTED_CIK = 789019

SUPPORTED_FORMS = {
    "10-K",
    "10-Q",
    "8-K",
    "10-K/A",
    "10-Q/A",
    "8-K/A",
}


def _get_live_clients() -> tuple[
    SECClient,
    SubmissionsClient,
    SECFilingsClient,
]:
    """
    Create the production SEC clients used by the live integration test.

    The same SECClient instance is deliberately shared by the
    submissions and filing clients so that all SEC traffic uses the
    same User-Agent, HTTP session, and rate limiter.
    """
    client = SECClient(
        name=SEC_NAME,
        email=SEC_EMAIL,
        organization=SEC_ORGANIZATION,
    )

    submissions_client = SubmissionsClient(client)
    filings_client = SECFilingsClient(client)

    return (
        client,
        submissions_client,
        filings_client,
    )


def _find_current_supported_filing(
    submissions_client: SubmissionsClient,
) -> SECFiling:
    """
    Retrieve MSFT's current SEC filing universe and select the first
    supported filing that has a primary document.

    The SEC Submissions API returns filings in reverse chronological
    order, so the first qualifying record represents the most recent
    supported filing available in the returned universe.
    """
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

    for filing in filings:
        if (
            filing.form in SUPPORTED_FORMS
            and filing.primary_document
        ):
            return filing

    raise AssertionError(
        f"No supported filing with a primary document was found "
        f"for {TEST_TICKER}."
    )


def test_msft_live_primary_document_retrieval() -> None:
    """
    Verify end-to-end live retrieval of an SEC primary filing document.

    Flow:

        SEC ticker
            ->
        Submissions API
            ->
        SECFiling
            ->
        EDGAR primary document
            ->
        raw bytes
            ->
        SHA-256 provenance hash
    """
    (
        client,
        submissions_client,
        filings_client,
    ) = _get_live_clients()

    try:
        filing = _find_current_supported_filing(
            submissions_client
        )

        assert filing.cik == EXPECTED_CIK
        assert filing.form in SUPPORTED_FORMS
        assert filing.accession_number
        assert filing.filing_date
        assert filing.primary_document

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
        assert document.source_url.startswith(
            "https://www.sec.gov/Archives/edgar/data/"
        )
        assert document.content_type
        assert document.content
        assert isinstance(document.content, bytes)

        assert len(document.content_hash) == 64
        assert all(
            character in "0123456789abcdef"
            for character in document.content_hash
        )

    finally:
        client.close()


def test_msft_live_complete_submission_retrieval() -> None:
    """
    Verify end-to-end live retrieval of the SEC complete-submission
    text file for a currently available supported MSFT filing.
    """
    (
        client,
        submissions_client,
        filings_client,
    ) = _get_live_clients()

    try:
        filing = _find_current_supported_filing(
            submissions_client
        )

        document = filings_client.get_complete_submission(
            filing
        )

        assert isinstance(
            document,
            SECFilingDocument,
        )

        assert document.filing is filing
        assert document.document_kind == "complete_submission"
        assert (
            document.document_name
            == f"{filing.accession_number}.txt"
        )

        assert document.source_url.startswith(
            "https://www.sec.gov/Archives/edgar/data/"
        )
        assert document.source_url.endswith(
            f"/{filing.accession_number}.txt"
        )

        assert document.content_type
        assert document.content
        assert isinstance(document.content, bytes)

        assert len(document.content_hash) == 64
        assert all(
            character in "0123456789abcdef"
            for character in document.content_hash
        )

    finally:
        client.close()