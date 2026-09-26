from __future__ import annotations

import hashlib
import os
from datetime import date

import pytest

from sec_client import SECClient
from sec_filings import SECFilingDocument
from sec_ownership import (
    SECOwnershipFiling,
    parse_ownership_filing,
)
from sec_submissions import SubmissionsClient


SEC_TEST_EMAIL = os.getenv("SEC_TEST_EMAIL")

pytestmark = pytest.mark.skipif(
    not SEC_TEST_EMAIL,
    reason="SEC_TEST_EMAIL is required for live SEC ownership tests.",
)


def _build_sec_client() -> SECClient:
    return SECClient(
        name="SEC Ownership Live Test",
        email=SEC_TEST_EMAIL,
        organization="Stock Research Multi-Agent System",
        requests_per_second=2.0,
        timeout=20.0,
    )


def test_live_msft_form4_ownership_parses() -> None:
    client = _build_sec_client()

    with client:
        submissions = SubmissionsClient(client)

        filings = submissions.get_recent_filings_by_cik(789019)

        ownership_filings = [
            filing
            for filing in filings
            if filing.form in {"4", "4/A"}
        ]

        assert ownership_filings

        filing = ownership_filings[0]
        assert filing.primary_document

        accession = filing.accession_number.replace("-", "")
        document_name = filing.primary_document

        if "/" in document_name:
            document_name = document_name.rsplit("/", 1)[-1]

        document_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{filing.cik}/{accession}/{document_name}"
        )

        content, content_type = client._get_content(document_url)

        assert content
        assert content_type == "text/xml"
        assert content.lstrip().startswith(b"<?xml")

        document = SECFilingDocument(
            filing=filing,
            document_name=document_name,
            document_kind="ownership",
            source_url=document_url,
            content_type=content_type,
            content_hash=hashlib.sha256(content).hexdigest(),
            content=content,
        )

        result = parse_ownership_filing(document)

        assert isinstance(result, SECOwnershipFiling)
        assert result.form in {"4", "4/A"}
        assert result.cik == "0000789019"
        assert result.accession_number == filing.accession_number
        assert result.filing_date == date.fromisoformat(filing.filing_date)
        assert result.provenance.content_sha256 == document.content_hash
        assert result.provenance.source_url == document.source_url
        assert result.source_document is document

        assert result.issuer_name
        assert result.issuer_cik == "0000789019"
        assert result.issuer_ticker == "MSFT"

        assert result.reporting_persons
        assert (
            result.non_derivative_ownership
            or result.derivative_ownership
        )
