"""
sec_filings.py

SEC EDGAR filing-document retrieval client.

Responsibilities:
- Retrieve primary filing documents from the SEC EDGAR archive.
- Retrieve complete submission text files from the SEC EDGAR archive.
- Validate filing metadata before constructing archive URLs.
- Support the initial filing universe:
    * 10-K
    * 10-Q
    * 8-K
    * amendments of the above (/A)
- Preserve the exact raw filing bytes returned by the SEC.
- Calculate a deterministic SHA-256 content hash.
- Return immutable filing-document records containing provenance metadata.

This module does NOT:
- discover filings
- retrieve filing metadata
- parse filing HTML/XML/text
- identify filing sections
- extract XBRL facts
- interpret financial information
- calculate investment metrics
- perform thesis analysis
- modify or normalize SEC filing contents

Architecture:

    SECClient
        |
        | HTTP / rate limiting / SEC identity
        v
    SECFilingsClient
        |
        +--> primary filing document
        |
        +--> complete submission text
        |
        v
    immutable SECFilingDocument
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from sec_client import (
    SECClient,
    SECClientError,
    SECConfigurationError,
)
from sec_submissions import SECFiling


class SECFilingError(Exception):
    """Base exception for SEC filing retrieval errors."""


class SECFilingDataError(SECFilingError):
    """
    Raised when filing metadata is malformed or the retrieved
    filing content violates the expected SEC retrieval contract.
    """


class SECUnsupportedFilingError(SECFilingError):
    """
    Raised when a filing form is outside the supported filing universe.
    """


@dataclass(frozen=True)
class SECFilingDocument:
    """
    Immutable representation of one retrieved SEC filing document.

    The complete raw document content is preserved as bytes so that
    downstream processing can independently decode and parse it.

    Attributes:
        filing:
            Immutable filing metadata produced by SubmissionsClient.

        document_name:
            Name of the retrieved document.

        document_kind:
            Retrieval type:
                - "primary"
                - "complete_submission"

        source_url:
            Exact SEC EDGAR archive URL used for retrieval.

        content_type:
            Content-Type returned by the SEC HTTP response.

        content_hash:
            SHA-256 hash of the exact raw content bytes.

        content:
            Exact raw document bytes returned by the SEC.
    """

    filing: SECFiling
    document_name: str
    document_kind: str
    source_url: str
    content_type: str
    content_hash: str
    content: bytes


class SECFilingsClient:
    """
    SEC EDGAR filing-document retrieval client.

    This class intentionally delegates all HTTP behavior to SECClient.
    It therefore does not create its own HTTP session, rate limiter,
    User-Agent, or retry mechanism.

    The client consumes an already-discovered SECFiling object rather
    than performing filing discovery itself.

    Supported filing forms:
        - 10-K
        - 10-Q
        - 8-K
        - 10-K/A
        - 10-Q/A
        - 8-K/A
    """

    EDGAR_ARCHIVES_BASE = (
        "https://www.sec.gov/Archives/edgar/data"
    )

    PRIMARY_DOCUMENT_KIND = "primary"
    COMPLETE_SUBMISSION_KIND = "complete_submission"

    SUPPORTED_BASE_FORMS = frozenset(
        {
            "10-K",
            "10-Q",
            "8-K",
        }
    )

    ACCESSION_PATTERN = re.compile(
        r"^\d{10}-\d{2}-\d{6}$"
    )

    def __init__(
        self,
        client: SECClient,
    ) -> None:
        """
        Initialize the filing-document client.

        Args:
            client:
                Existing SECClient responsible for SEC HTTP transport,
                rate limiting, User-Agent handling, and response retrieval.

        Raises:
            SECConfigurationError:
                If client is not a valid SECClient instance.
        """
        if not isinstance(client, SECClient):
            raise SECConfigurationError(
                "SECFilingsClient requires a valid SECClient instance."
            )

        self.client = client

    @classmethod
    def _get_base_form(
        cls,
        form: str,
    ) -> str:
        """
        Return the non-amended base filing form.

        SEC amendment forms use the /A suffix, for example:
            10-K/A -> 10-K
            10-Q/A -> 10-Q
            8-K/A  -> 8-K

        The original form string is not modified elsewhere; this helper
        exists only for support validation.
        """
        if not isinstance(form, str) or not form:
            raise SECFilingDataError(
                "Filing form must be a non-empty string."
            )

        if form.endswith("/A"):
            return form[:-2]

        return form

    @classmethod
    def _validate_supported_form(
        cls,
        form: str,
    ) -> None:
        """
        Validate that the filing form belongs to the supported universe.

        Raises:
            SECFilingDataError:
                If the form is missing or invalid.

            SECUnsupportedFilingError:
                If the form is structurally valid but unsupported.
        """
        base_form = cls._get_base_form(form)

        if base_form not in cls.SUPPORTED_BASE_FORMS:
            raise SECUnsupportedFilingError(
                f"Unsupported SEC filing form: {form!r}. "
                f"Supported base forms: "
                f"{sorted(cls.SUPPORTED_BASE_FORMS)}."
            )

    @classmethod
    def _validate_filing(
        cls,
        filing: SECFiling,
    ) -> None:
        """
        Validate the structural integrity of an SECFiling record.

        Validation occurs before any archive URL is constructed.

        Raises:
            SECFilingDataError:
                If filing metadata is malformed.

            SECUnsupportedFilingError:
                If the filing form is unsupported.
        """
        if not isinstance(filing, SECFiling):
            raise SECFilingDataError(
                "filing must be an SECFiling instance."
            )

        if (
            not isinstance(filing.cik, int)
            or isinstance(filing.cik, bool)
            or filing.cik < 0
        ):
            raise SECFilingDataError(
                f"Filing CIK must be a non-negative integer; "
                f"received {filing.cik!r}."
            )

        if not isinstance(filing.form, str) or not filing.form:
            raise SECFilingDataError(
                "Filing form must be a non-empty string."
            )

        cls._validate_supported_form(filing.form)

        if (
            not isinstance(filing.accession_number, str)
            or not filing.accession_number
        ):
            raise SECFilingDataError(
                "Filing accession number must be a non-empty string."
            )

        if not cls.ACCESSION_PATTERN.fullmatch(
            filing.accession_number
        ):
            raise SECFilingDataError(
                "Filing accession number has an invalid format: "
                f"{filing.accession_number!r}."
            )

        if (
            not isinstance(filing.filing_date, str)
            or not filing.filing_date
        ):
            raise SECFilingDataError(
                "Filing date must be a non-empty string."
            )

        if filing.report_date is not None and not isinstance(
            filing.report_date,
            str,
        ):
            raise SECFilingDataError(
                "Filing report date must be a string or None."
            )

        if filing.primary_document is not None and not isinstance(
            filing.primary_document,
            str,
        ):
            raise SECFilingDataError(
                "Filing primary document must be a string or None."
            )

        if not isinstance(filing.is_amendment, bool):
            raise SECFilingDataError(
                "Filing is_amendment must be a boolean."
            )

        if filing.file_number is not None and not isinstance(
            filing.file_number,
            str,
        ):
            raise SECFilingDataError(
                "Filing file number must be a string or None."
            )

        if not isinstance(filing.filing_url, str):
            raise SECFilingDataError(
                "Filing URL must be a string."
            )

    @staticmethod
    def _validate_document_name(
        document_name: str,
    ) -> None:
        """
        Validate a SEC archive document filename.

        SEC primary-document metadata represents a filename rather than
        an arbitrary filesystem path. Path separators and traversal
        components are therefore rejected before URL construction.

        Raises:
            SECFilingDataError:
                If the document name is unsafe or malformed.
        """
        if not isinstance(document_name, str) or not document_name:
            raise SECFilingDataError(
                "Document name must be a non-empty string."
            )

        if "\x00" in document_name:
            raise SECFilingDataError(
                "Document name contains a NUL character."
            )

        if "/" in document_name or "\\" in document_name:
            raise SECFilingDataError(
                "Document name must not contain path separators."
            )

        if document_name in {".", ".."}:
            raise SECFilingDataError(
                "Document name must not be a path traversal component."
            )

    @staticmethod
    def _normalize_accession(
        accession_number: str,
    ) -> str:
        """
        Convert an SEC accession number to its archive directory form.

        Example:
            0000789019-24-000123
            ->
            000078901924000123
        """
        return accession_number.replace("-", "")

    @classmethod
    def _build_primary_document_url(
        cls,
        filing: SECFiling,
    ) -> str:
        """
        Build the deterministic SEC EDGAR URL for the primary document.

        The URL is constructed from validated filing metadata rather than
        trusting the URL stored in the SECFiling object.
        """
        if not filing.primary_document:
            raise SECFilingDataError(
                "Primary document is required for primary-document "
                "retrieval."
            )

        cls._validate_document_name(
            filing.primary_document
        )

        accession_path = cls._normalize_accession(
            filing.accession_number
        )

        encoded_document_name = quote(
            filing.primary_document,
            safe="._-",
        )

        return (
            f"{cls.EDGAR_ARCHIVES_BASE}/"
            f"{filing.cik}/"
            f"{accession_path}/"
            f"{encoded_document_name}"
        )

    @classmethod
    def _build_complete_submission_url(
        cls,
        filing: SECFiling,
    ) -> str:
        """
        Build the deterministic SEC EDGAR complete-submission URL.

        The SEC archive stores the complete submission text using the
        original dashed accession number followed by '.txt'.
        """
        accession_path = cls._normalize_accession(
            filing.accession_number
        )

        accession_filename = (
            f"{filing.accession_number}.txt"
        )

        return (
            f"{cls.EDGAR_ARCHIVES_BASE}/"
            f"{filing.cik}/"
            f"{accession_path}/"
            f"{accession_filename}"
        )

    @staticmethod
    def _calculate_content_hash(
        content: bytes,
    ) -> str:
        """
        Calculate the SHA-256 hash of the exact raw content bytes.
        """
        return hashlib.sha256(content).hexdigest()

    def _retrieve_document(
        self,
        filing: SECFiling,
        document_name: str,
        document_kind: str,
        source_url: str,
    ) -> SECFilingDocument:
        """
        Retrieve one filing document and construct its immutable record.

        HTTP transport is delegated entirely to SECClient.

        Raises:
            SECFilingDataError:
                If SECClient returns data that violates the expected
                raw-content contract.

            SECClientError:
                Propagated unchanged when SECClient encounters an
                HTTP, transport, or SEC response error.
        """
        try:
            content, content_type = self.client._get_content(
                source_url
            )
        except SECClientError:
            raise

        if not isinstance(content, bytes):
            raise SECFilingDataError(
                "SECClient returned filing content that is not bytes."
            )

        if not content:
            raise SECFilingDataError(
                "SECClient returned empty filing content."
            )

        if not isinstance(content_type, str):
            raise SECFilingDataError(
                "SECClient returned an invalid content type."
            )

        content_hash = self._calculate_content_hash(
            content
        )

        return SECFilingDocument(
            filing=filing,
            document_name=document_name,
            document_kind=document_kind,
            source_url=source_url,
            content_type=content_type,
            content_hash=content_hash,
            content=content,
        )

    def get_primary_document(
        self,
        filing: SECFiling,
    ) -> SECFilingDocument:
        """
        Retrieve the primary document for an SEC filing.

        This method retrieves only the primary filing document. It does
        not retrieve exhibits or the complete submission text.

        Args:
            filing:
                Filing metadata returned by SubmissionsClient.

        Returns:
            Immutable SECFilingDocument containing the exact raw bytes,
            source URL, content type, and SHA-256 hash.

        Raises:
            SECFilingDataError:
                If filing metadata or primary-document metadata is invalid.

            SECUnsupportedFilingError:
                If the filing form is outside the supported universe.

            SECClientError:
                If the underlying SEC request fails.
        """
        self._validate_filing(filing)

        if not filing.primary_document:
            raise SECFilingDataError(
                "Cannot retrieve primary document because the filing "
                "does not specify a primary document."
            )

        source_url = self._build_primary_document_url(
            filing
        )

        return self._retrieve_document(
            filing=filing,
            document_name=filing.primary_document,
            document_kind=self.PRIMARY_DOCUMENT_KIND,
            source_url=source_url,
        )

    def get_complete_submission(
        self,
        filing: SECFiling,
    ) -> SECFilingDocument:
        """
        Retrieve the complete raw EDGAR submission text file.

        The complete submission may contain:
        - submission metadata
        - primary filing document
        - exhibits
        - additional submitted documents

        No parsing or normalization is performed.

        Args:
            filing:
                Filing metadata returned by SubmissionsClient.

        Returns:
            Immutable SECFilingDocument containing the exact raw
            submission bytes and SHA-256 hash.

        Raises:
            SECFilingDataError:
                If filing metadata is malformed.

            SECUnsupportedFilingError:
                If the filing form is outside the supported universe.

            SECClientError:
                If the underlying SEC request fails.
        """
        self._validate_filing(filing)

        source_url = self._build_complete_submission_url(
            filing
        )

        document_name = (
            f"{filing.accession_number}.txt"
        )

        return self._retrieve_document(
            filing=filing,
            document_name=document_name,
            document_kind=self.COMPLETE_SUBMISSION_KIND,
            source_url=source_url,
        )