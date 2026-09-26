"""
test_sec_filings_mock.py

Unit tests for sec_filings.py.

Test strategy:
- No live SEC network requests.
- Use a real SECClient instance to preserve the production
  constructor contract.
- Mock SECClient._get_content() at the transport boundary.
- Validate URL construction, filing validation, raw-content
  preservation, SHA-256 hashing, supported forms, amendments,
  security checks, and SEC error propagation.
"""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError

import pytest

from sec_client import (
    SECClient,
    SECClientError,
    SECConfigurationError,
)
from sec_filings import (
    SECUnsupportedFilingError,
    SECFilingDataError,
    SECFilingDocument,
    SECFilingsClient,
)
from sec_submissions import SECFiling


SEC_NAME = "Schwab Market Data Pilot"
SEC_EMAIL = "test@example.com"
SEC_ORGANIZATION = "Schwab Market Data Pilot"


@pytest.fixture
def sec_client() -> SECClient:
    """
    Create a real SECClient for unit testing.

    Network access is never performed because _get_content()
    is replaced by each test that requires transport behavior.
    """
    return SECClient(
        name=SEC_NAME,
        email=SEC_EMAIL,
        organization=SEC_ORGANIZATION,
    )


@pytest.fixture
def filings_client(
    sec_client: SECClient,
) -> SECFilingsClient:
    """
    Create the production filing client under test.
    """
    return SECFilingsClient(sec_client)


@pytest.fixture
def filing() -> SECFiling:
    """
    Representative 10-K filing metadata.
    """
    return SECFiling(
        cik=789019,
        form="10-K",
        accession_number="0000789019-24-000123",
        filing_date="2024-01-31",
        report_date="2023-12-31",
        primary_document="msft-20231231.htm",
        is_amendment=False,
        file_number="001-37845",
        filing_url=(
            "https://www.sec.gov/Archives/edgar/data/"
            "789019/000078901924000123/msft-20231231.htm"
        ),
    )


def test_requires_sec_client() -> None:
    """
    SECFilingsClient must reject invalid transport clients.
    """
    with pytest.raises(SECConfigurationError):
        SECFilingsClient(object())  # type: ignore[arg-type]


def test_primary_document_retrieval(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Primary-document retrieval must:
    - construct the deterministic SEC archive URL
    - delegate retrieval to SECClient
    - preserve exact raw bytes
    - preserve content type
    - calculate the correct SHA-256 hash
    - return an immutable SECFilingDocument
    """
    content = b"<html><body>SEC filing</body></html>"
    content_type = "text/html; charset=UTF-8"

    calls: list[str] = []

    def mock_get_content(
        url: str,
    ) -> tuple[bytes, str]:
        calls.append(url)
        return content, content_type

    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        mock_get_content,
    )

    result = filings_client.get_primary_document(
        filing
    )

    expected_url = (
        "https://www.sec.gov/Archives/edgar/data/"
        "789019/000078901924000123/msft-20231231.htm"
    )

    expected_hash = hashlib.sha256(content).hexdigest()

    assert isinstance(result, SECFilingDocument)
    assert result.filing == filing
    assert result.document_name == "msft-20231231.htm"
    assert result.document_kind == "primary"
    assert result.source_url == expected_url
    assert result.content_type == content_type
    assert result.content_hash == expected_hash
    assert result.content == content
    assert calls == [expected_url]


def test_complete_submission_retrieval(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Complete-submission retrieval must use the SEC accession-number
    .txt archive path and preserve the exact raw submission bytes.
    """
    content = (
        b"SEC submission header\n"
        b"<DOCUMENT>\n"
        b"filing content\n"
        b"</DOCUMENT>\n"
    )
    content_type = "text/plain; charset=UTF-8"

    calls: list[str] = []

    def mock_get_content(
        url: str,
    ) -> tuple[bytes, str]:
        calls.append(url)
        return content, content_type

    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        mock_get_content,
    )

    result = filings_client.get_complete_submission(
        filing
    )

    expected_url = (
        "https://www.sec.gov/Archives/edgar/data/"
        "789019/000078901924000123/"
        "0000789019-24-000123.txt"
    )

    expected_hash = hashlib.sha256(content).hexdigest()

    assert isinstance(result, SECFilingDocument)
    assert result.filing == filing
    assert result.document_name == "0000789019-24-000123.txt"
    assert result.document_kind == "complete_submission"
    assert result.source_url == expected_url
    assert result.content_type == content_type
    assert result.content_hash == expected_hash
    assert result.content == content
    assert calls == [expected_url]


@pytest.mark.parametrize(
    "form",
    [
        "10-K",
        "10-Q",
        "8-K",
        "10-K/A",
        "10-Q/A",
        "8-K/A",
    ],
)
def test_supported_filing_forms(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    form: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    All initially supported forms, including amendments, must be
    accepted.
    """
    supported_filing = SECFiling(
        cik=filing.cik,
        form=form,
        accession_number=filing.accession_number,
        filing_date=filing.filing_date,
        report_date=filing.report_date,
        primary_document=filing.primary_document,
        is_amendment=form.endswith("/A"),
        file_number=filing.file_number,
        filing_url=filing.filing_url,
    )

    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        lambda url: (b"filing", "text/html"),
    )

    result = filings_client.get_primary_document(
        supported_filing
    )

    assert result.filing.form == form


@pytest.mark.parametrize(
    "form",
    [
        "20-F",
        "6-K",
        "DEF 14A",
        "S-1",
        "4",
        "13F-HR",
    ],
)
def test_unsupported_filing_forms_are_rejected(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    form: str,
) -> None:
    """
    Filing forms outside the initial SEC-4 scope must be rejected
    before any network request occurs.
    """
    unsupported_filing = SECFiling(
        cik=filing.cik,
        form=form,
        accession_number=filing.accession_number,
        filing_date=filing.filing_date,
        report_date=filing.report_date,
        primary_document=filing.primary_document,
        is_amendment=False,
        file_number=filing.file_number,
        filing_url=filing.filing_url,
    )

    with pytest.raises(SECUnsupportedFilingError):
        filings_client.get_primary_document(
            unsupported_filing
        )


def test_invalid_filing_type_is_rejected(
    filings_client: SECFilingsClient,
) -> None:
    """
    Retrieval methods must reject arbitrary objects rather than
    attempting to access attributes dynamically.
    """
    with pytest.raises(SECFilingDataError):
        filings_client.get_primary_document(
            object()  # type: ignore[arg-type]
        )


def test_invalid_cik_is_rejected(
    filings_client: SECFilingsClient,
    filing: SECFiling,
) -> None:
    """
    Boolean and negative CIK values must not be accepted as valid
    filing metadata.
    """
    invalid_values = [
        -1,
        True,
        False,
    ]

    for invalid_cik in invalid_values:
        invalid_filing = SECFiling(
            cik=invalid_cik,  # type: ignore[arg-type]
            form=filing.form,
            accession_number=filing.accession_number,
            filing_date=filing.filing_date,
            report_date=filing.report_date,
            primary_document=filing.primary_document,
            is_amendment=filing.is_amendment,
            file_number=filing.file_number,
            filing_url=filing.filing_url,
        )

        with pytest.raises(SECFilingDataError):
            filings_client.get_primary_document(
                invalid_filing
            )


@pytest.mark.parametrize(
    "accession_number",
    [
        "",
        "123",
        "000078901924000123",
        "0000789019-24-123",
        "0000789019/24/000123",
        "0000789019-24-000123/",
    ],
)
def test_invalid_accession_number_is_rejected(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    accession_number: str,
) -> None:
    """
    Only the standard SEC dashed accession-number format is accepted.
    """
    invalid_filing = SECFiling(
        cik=filing.cik,
        form=filing.form,
        accession_number=accession_number,
        filing_date=filing.filing_date,
        report_date=filing.report_date,
        primary_document=filing.primary_document,
        is_amendment=filing.is_amendment,
        file_number=filing.file_number,
        filing_url=filing.filing_url,
    )

    with pytest.raises(SECFilingDataError):
        filings_client.get_primary_document(
            invalid_filing
        )


def test_missing_primary_document_is_rejected(
    filings_client: SECFilingsClient,
    filing: SECFiling,
) -> None:
    """
    Primary-document retrieval requires a primaryDocument value.
    """
    invalid_filing = SECFiling(
        cik=filing.cik,
        form=filing.form,
        accession_number=filing.accession_number,
        filing_date=filing.filing_date,
        report_date=filing.report_date,
        primary_document=None,
        is_amendment=filing.is_amendment,
        file_number=filing.file_number,
        filing_url="",
    )

    with pytest.raises(SECFilingDataError):
        filings_client.get_primary_document(
            invalid_filing
        )


@pytest.mark.parametrize(
    "document_name",
    [
        "../malicious.htm",
        "../../malicious.htm",
        "subdir/file.htm",
        r"subdir\file.htm",
        ".",
        "..",
        "safe\x00file.htm",
    ],
)
def test_unsafe_primary_document_name_is_rejected(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    document_name: str,
) -> None:
    """
    Primary document metadata must not be allowed to introduce
    path traversal or path-separator components into archive URLs.
    """
    invalid_filing = SECFiling(
        cik=filing.cik,
        form=filing.form,
        accession_number=filing.accession_number,
        filing_date=filing.filing_date,
        report_date=filing.report_date,
        primary_document=document_name,
        is_amendment=filing.is_amendment,
        file_number=filing.file_number,
        filing_url=filing.filing_url,
    )

    with pytest.raises(SECFilingDataError):
        filings_client.get_primary_document(
            invalid_filing
        )


def test_filing_url_field_is_not_trusted(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The retrieval client must construct the archive URL from validated
    filing metadata rather than trusting the filing_url field.

    This prevents a malformed or externally supplied filing_url from
    redirecting retrieval to an unintended host.
    """
    malicious_filing = SECFiling(
        cik=filing.cik,
        form=filing.form,
        accession_number=filing.accession_number,
        filing_date=filing.filing_date,
        report_date=filing.report_date,
        primary_document=filing.primary_document,
        is_amendment=filing.is_amendment,
        file_number=filing.file_number,
        filing_url="https://attacker.example/malicious-document.htm",
    )

    calls: list[str] = []

    def mock_get_content(
        url: str,
    ) -> tuple[bytes, str]:
        calls.append(url)
        return b"safe filing", "text/html"

    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        mock_get_content,
    )

    result = filings_client.get_primary_document(
        malicious_filing
    )

    expected_url = (
        "https://www.sec.gov/Archives/edgar/data/"
        "789019/000078901924000123/msft-20231231.htm"
    )

    assert result.source_url == expected_url
    assert calls == [expected_url]
    assert "attacker.example" not in calls[0]


def test_primary_document_url_encodes_special_filename_characters(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Filename characters that have URL semantics must be percent-encoded
    while ordinary filename characters remain readable.
    """
    special_filing = SECFiling(
        cik=filing.cik,
        form=filing.form,
        accession_number=filing.accession_number,
        filing_date=filing.filing_date,
        report_date=filing.report_date,
        primary_document="msft filing #1.htm",
        is_amendment=filing.is_amendment,
        file_number=filing.file_number,
        filing_url="",
    )

    calls: list[str] = []

    def mock_get_content(
        url: str,
    ) -> tuple[bytes, str]:
        calls.append(url)
        return b"filing", "text/html"

    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        mock_get_content,
    )

    filings_client.get_primary_document(
        special_filing
    )

    assert calls == [
        (
            "https://www.sec.gov/Archives/edgar/data/"
            "789019/000078901924000123/"
            "msft%20filing%20%231.htm"
        )
    ]


def test_sec_client_errors_are_propagated(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Transport-layer SECClient errors must propagate unchanged so
    callers retain the original failure type and context.
    """
    expected_error = SECClientError(
        "SEC request failed."
    )

    def mock_get_content(
        url: str,
    ) -> tuple[bytes, str]:
        raise expected_error

    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        mock_get_content,
    )

    with pytest.raises(SECClientError) as exc_info:
        filings_client.get_primary_document(
            filing
        )

    assert exc_info.value is expected_error


def test_empty_content_is_rejected(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Empty raw filing content must never be represented as a successful
    filing-document retrieval.
    """
    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        lambda url: (b"", "text/html"),
    )

    with pytest.raises(SECFilingDataError):
        filings_client.get_primary_document(
            filing
        )


def test_non_bytes_content_is_rejected(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Filing content must remain raw bytes at the retrieval boundary.
    """
    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        lambda url: ("filing text", "text/html"),
    )

    with pytest.raises(SECFilingDataError):
        filings_client.get_primary_document(
            filing
        )


def test_invalid_content_type_is_rejected(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The SECClient content-type contract must be preserved.
    """
    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        lambda url: (b"filing", None),
    )

    with pytest.raises(SECFilingDataError):
        filings_client.get_primary_document(
            filing
        )


def test_raw_content_is_not_modified(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Retrieval must preserve the exact byte sequence returned by the SEC.
    No decoding, whitespace normalization, newline conversion, or
    character replacement may occur at this layer.
    """
    content = (
        b"\xef\xbb\xbf"
        b"<html>\r\n"
        b"  SEC filing\r\n"
        b"</html>\r\n"
    )

    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        lambda url: (content, "text/html"),
    )

    result = filings_client.get_primary_document(
        filing
    )

    assert result.content == content
    assert result.content_hash == hashlib.sha256(
        content
    ).hexdigest()


def test_content_hash_is_sha256(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The content hash must be the SHA-256 digest of the exact raw bytes.
    """
    content = b"deterministic SEC filing content"

    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        lambda url: (content, "text/plain"),
    )

    result = filings_client.get_primary_document(
        filing
    )

    expected_hash = hashlib.sha256(
        content
    ).hexdigest()

    assert result.content_hash == expected_hash
    assert len(result.content_hash) == 64
    assert result.content_hash == result.content_hash.lower()


def test_complete_submission_does_not_require_primary_document(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Complete-submission retrieval depends only on accession metadata;
    it does not require primaryDocument.
    """
    submission_only_filing = SECFiling(
        cik=filing.cik,
        form=filing.form,
        accession_number=filing.accession_number,
        filing_date=filing.filing_date,
        report_date=filing.report_date,
        primary_document=None,
        is_amendment=filing.is_amendment,
        file_number=filing.file_number,
        filing_url="",
    )

    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        lambda url: (b"complete submission", "text/plain"),
    )

    result = filings_client.get_complete_submission(
        submission_only_filing
    )

    assert result.document_kind == "complete_submission"
    assert result.content == b"complete submission"


def test_complete_submission_url_uses_dashed_accession_filename(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The complete submission filename must retain the SEC's dashed
    accession number while the directory removes the dashes.
    """
    calls: list[str] = []

    def mock_get_content(
        url: str,
    ) -> tuple[bytes, str]:
        calls.append(url)
        return b"submission", "text/plain"

    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        mock_get_content,
    )

    filings_client.get_complete_submission(
        filing
    )

    assert calls == [
        (
            "https://www.sec.gov/Archives/edgar/data/"
            "789019/000078901924000123/"
            "0000789019-24-000123.txt"
        )
    ]


def test_result_is_immutable(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    SECFilingDocument must remain immutable after retrieval so that
    provenance metadata and content cannot be silently altered.
    """
    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        lambda url: (b"filing", "text/html"),
    )

    result = filings_client.get_primary_document(
        filing
    )

    with pytest.raises(FrozenInstanceError):
        result.content_hash = "modified"  # type: ignore[misc]


def test_filing_metadata_is_preserved_exactly(
    filings_client: SECFilingsClient,
    filing: SECFiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The retrieval layer must retain the exact SECFiling metadata supplied
    by the discovery layer.
    """
    monkeypatch.setattr(
        filings_client.client,
        "_get_content",
        lambda url: (b"filing", "text/html"),
    )

    result = filings_client.get_primary_document(
        filing
    )

    assert result.filing is filing
    assert result.filing.cik == filing.cik
    assert result.filing.form == filing.form
    assert (
        result.filing.accession_number
        == filing.accession_number
    )
    assert result.filing.filing_date == filing.filing_date
    assert result.filing.report_date == filing.report_date
    assert (
        result.filing.primary_document
        == filing.primary_document
    )
    assert result.filing.is_amendment == filing.is_amendment
    assert result.filing.file_number == filing.file_number