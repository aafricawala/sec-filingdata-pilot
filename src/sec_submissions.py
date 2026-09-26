"""
sec_submissions.py

SEC EDGAR Submissions API client.

Responsibilities:
- Retrieve issuer filing metadata from the SEC Submissions API.
- Resolve ticker -> CIK through SECClient.
- Validate the basic Submissions response structure.
- Normalize filing metadata into deterministic records.
- Support recent filings and SEC historical filing files.
- Provide filing-discovery capabilities only.

This module does NOT:
- retrieve filing documents
- parse filing HTML/text
- extract financial metrics
- interpret XBRL
- perform thesis analysis
- calculate investment metrics
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sec_client import (
    SECClient,
    SECClientError,
    SECConfigurationError,
    SECDataError,
    SECTickerNotFoundError,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SECSubmissionsError(Exception):
    """Base exception for SEC Submissions errors."""


class SECSubmissionsDataError(SECSubmissionsError):
    """Raised when the SEC Submissions response is malformed."""


# ---------------------------------------------------------------------------
# Filing record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SECFiling:
    """
    Immutable representation of one SEC filing metadata record.

    This class contains discovery metadata only. It does not contain
    filing document contents.
    """

    cik: int
    form: str
    accession_number: str
    filing_date: str
    report_date: Optional[str]
    primary_document: Optional[str]
    is_amendment: bool
    file_number: Optional[str]
    filing_url: str


# ---------------------------------------------------------------------------
# SubmissionsClient
# ---------------------------------------------------------------------------

class SubmissionsClient:
    """
    SEC EDGAR Submissions API client.

    Uses SECClient for:
        - SEC User-Agent
        - HTTP session
        - rate limiting
        - request handling
        - ticker -> CIK resolution
        - JSON validation

    This class is intentionally limited to filing discovery and metadata.
    """

    SUBMISSIONS_URL = (
        "https://data.sec.gov/submissions/CIK{cik}.json"
    )

    EDGAR_ARCHIVES_BASE = (
        "https://www.sec.gov/Archives/edgar/data"
    )

    def __init__(
        self,
        client: SECClient,
    ) -> None:
        if not isinstance(client, SECClient):
            raise SECConfigurationError(
                "SubmissionsClient requires a valid SECClient instance."
            )

        self.client = client

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _validate_submissions_root(
        data: Dict[str, Any],
    ) -> None:
        """
        Validate the minimum expected SEC Submissions structure.
        """

        if not isinstance(data, dict):
            raise SECSubmissionsDataError(
                "SEC Submissions response must be a JSON object."
            )

        required_keys = {
            "name",
            "cik",
            "filings",
        }

        missing = required_keys - data.keys()

        if missing:
            raise SECSubmissionsDataError(
                "SEC Submissions response is missing required "
                f"fields: {sorted(missing)}."
            )

        if not isinstance(data["filings"], dict):
            raise SECSubmissionsDataError(
                "SEC Submissions 'filings' field must be an object."
            )

    @staticmethod
    def _validate_recent_filings(
        filings: Dict[str, Any],
    ) -> None:
        """
        Validate the SEC recent-filings arrays.
        """

        recent = filings.get("recent")

        if not isinstance(recent, dict):
            raise SECSubmissionsDataError(
                "SEC Submissions 'filings.recent' must be an object."
            )

        required_arrays = {
            "form",
            "accessionNumber",
            "filingDate",
            "reportDate",
            "primaryDocument",
        }

        missing = [
            key
            for key in required_arrays
            if key not in recent
        ]

        if missing:
            raise SECSubmissionsDataError(
                "SEC Submissions recent filings are missing "
                f"required fields: {sorted(missing)}."
            )

        for key in required_arrays:
            if not isinstance(recent[key], list):
                raise SECSubmissionsDataError(
                    f"SEC Submissions field "
                    f"'filings.recent.{key}' must be a list."
                )

    @staticmethod
    def _normalize_accession(
        accession_number: str,
    ) -> str:
        """
        Remove hyphens from an SEC accession number for EDGAR archive URLs.
        """

        return accession_number.replace("-", "")

    @classmethod
    def _build_filing_url(
        cls,
        cik: int,
        accession_number: str,
        primary_document: str,
    ) -> str:
        """
        Build the deterministic EDGAR primary-document URL.
        """

        accession_path = cls._normalize_accession(accession_number)

        return (
            f"{cls.EDGAR_ARCHIVES_BASE}/"
            f"{cik}/{accession_path}/{primary_document}"
        )

    # -------------------------------------------------------------------
    # Public API: retrieve submissions
    # -------------------------------------------------------------------

    def get_submissions_by_cik(
        self,
        cik: int,
    ) -> Dict[str, Any]:
        """
        Retrieve raw SEC Submissions metadata for a CIK.

        The returned dictionary is the raw SEC response after basic
        structural validation. No filing interpretation is performed.
        """

        if not isinstance(cik, int) or isinstance(cik, bool):
            raise SECConfigurationError(
                f"CIK must be an integer; received {cik!r}."
            )

        if cik < 0:
            raise SECConfigurationError(
                f"CIK must be non-negative; received {cik}."
            )

        cik10 = f"{cik:010d}"

        url = self.SUBMISSIONS_URL.format(cik=cik10)

        try:
            data = self.client._get_json(url)
        except SECClientError:
            raise

        self._validate_submissions_root(data)

        self._validate_recent_filings(
            data["filings"]
        )

        return data

    # -------------------------------------------------------------------
    # Public API: ticker
    # -------------------------------------------------------------------

    def get_submissions_by_ticker(
        self,
        ticker: str,
    ) -> Dict[str, Any]:
        """
        Resolve ticker -> CIK and retrieve raw SEC Submissions metadata.
        """

        if not isinstance(ticker, str) or not ticker.strip():
            raise SECConfigurationError(
                "Ticker must be a non-empty string."
            )

        try:
            cik = self.client.get_cik_by_ticker(ticker)
        except SECTickerNotFoundError:
            raise
        except SECClientError:
            raise

        return self.get_submissions_by_cik(cik)

    # -------------------------------------------------------------------
    # Public API: recent filings
    # -------------------------------------------------------------------

    def get_recent_filings_by_cik(
        self,
        cik: int,
    ) -> List[SECFiling]:
        """
        Convert the SEC recent filing arrays into immutable SECFiling records.
        """

        data = self.get_submissions_by_cik(cik)

        recent = data["filings"]["recent"]

        cik_value = int(data["cik"])

        forms = recent["form"]
        accessions = recent["accessionNumber"]
        filing_dates = recent["filingDate"]
        report_dates = recent["reportDate"]
        primary_documents = recent["primaryDocument"]

        lengths = {
            len(forms),
            len(accessions),
            len(filing_dates),
            len(report_dates),
            len(primary_documents),
        }

        if len(lengths) != 1:
            raise SECSubmissionsDataError(
                "SEC Submissions recent filing arrays have "
                "inconsistent lengths."
            )

        file_numbers = recent.get("fileNumber")

        if file_numbers is not None and not isinstance(
            file_numbers,
            list,
        ):
            raise SECSubmissionsDataError(
                "SEC Submissions 'fileNumber' must be a list."
            )

        result: List[SECFiling] = []

        for index in range(len(forms)):
            form = forms[index]
            accession = accessions[index]
            filing_date = filing_dates[index]
            report_date = report_dates[index]
            primary_document = primary_documents[index]

            if not isinstance(form, str):
                raise SECSubmissionsDataError(
                    f"Invalid form at filing index {index}."
                )

            if not isinstance(accession, str):
                raise SECSubmissionsDataError(
                    f"Invalid accession number at filing index {index}."
                )

            if not isinstance(filing_date, str):
                raise SECSubmissionsDataError(
                    f"Invalid filing date at filing index {index}."
                )

            if report_date is not None and not isinstance(
                report_date,
                str,
            ):
                raise SECSubmissionsDataError(
                    f"Invalid report date at filing index {index}."
                )

            if primary_document is not None and not isinstance(
                primary_document,
                str,
            ):
                raise SECSubmissionsDataError(
                    f"Invalid primary document at filing index {index}."
                )

            file_number = None

            if file_numbers is not None:
                file_number = file_numbers[index]

            is_amendment = form.endswith("/A")

            filing_url = ""

            if primary_document:
                filing_url = self._build_filing_url(
                    cik=cik_value,
                    accession_number=accession,
                    primary_document=primary_document,
                )

            result.append(
                SECFiling(
                    cik=cik_value,
                    form=form,
                    accession_number=accession,
                    filing_date=filing_date,
                    report_date=report_date,
                    primary_document=primary_document,
                    is_amendment=is_amendment,
                    file_number=file_number,
                    filing_url=filing_url,
                )
            )

        return result

    def get_recent_filings_by_ticker(
        self,
        ticker: str,
    ) -> List[SECFiling]:
        """
        Resolve ticker -> CIK and return recent filing metadata.
        """

        cik = self.client.get_cik_by_ticker(ticker)

        return self.get_recent_filings_by_cik(cik)