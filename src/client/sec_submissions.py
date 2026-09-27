from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from client.sec_client import SECClient, SECClientError, SECConfigurationError, SECDataError, SECTickerNotFoundError

class SECSubmissionsError(Exception):
    pass

class SECSubmissionsDataError(SECSubmissionsError):
    pass

@dataclass(frozen=True)
class SECFiling:
    cik: int
    form: str
    accession_number: str
    filing_date: str
    report_date: Optional[str]
    primary_document: Optional[str]
    is_amendment: bool
    file_number: Optional[str]
    filing_url: str

class SubmissionsClient:
    SUBMISSIONS_URL = 'https://data.sec.gov/submissions/CIK{cik}.json'
    EDGAR_ARCHIVES_BASE = 'https://www.sec.gov/Archives/edgar/data'

    def __init__(self, client: SECClient) -> None:
        if not isinstance(client, SECClient):
            raise SECConfigurationError('SubmissionsClient requires a valid SECClient instance.')
        self.client = client

    @staticmethod
    def _validate_submissions_root(data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise SECSubmissionsDataError('SEC Submissions response must be a JSON object.')
        required_keys = {'name', 'cik', 'filings'}
        missing = required_keys - data.keys()
        if missing:
            raise SECSubmissionsDataError(f'SEC Submissions response is missing required fields: {sorted(missing)}.')
        if not isinstance(data['filings'], dict):
            raise SECSubmissionsDataError("SEC Submissions 'filings' field must be an object.")

    @staticmethod
    def _validate_recent_filings(filings: Dict[str, Any]) -> None:
        recent = filings.get('recent')
        if not isinstance(recent, dict):
            raise SECSubmissionsDataError("SEC Submissions 'filings.recent' must be an object.")
        required_arrays = {'form', 'accessionNumber', 'filingDate', 'reportDate', 'primaryDocument'}
        missing = [key for key in required_arrays if key not in recent]
        if missing:
            raise SECSubmissionsDataError(f'SEC Submissions recent filings are missing required fields: {sorted(missing)}.')
        for key in required_arrays:
            if not isinstance(recent[key], list):
                raise SECSubmissionsDataError(f"SEC Submissions field 'filings.recent.{key}' must be a list.")

    @staticmethod
    def _normalize_accession(accession_number: str) -> str:
        return accession_number.replace('-', '')

    @classmethod
    def _build_filing_url(cls, cik: int, accession_number: str, primary_document: str) -> str:
        accession_path = cls._normalize_accession(accession_number)
        return f'{cls.EDGAR_ARCHIVES_BASE}/{cik}/{accession_path}/{primary_document}'

    def get_submissions_by_cik(self, cik: int) -> Dict[str, Any]:
        if not isinstance(cik, int) or isinstance(cik, bool):
            raise SECConfigurationError(f'CIK must be an integer; received {cik!r}.')
        if cik < 0:
            raise SECConfigurationError(f'CIK must be non-negative; received {cik}.')
        cik10 = f'{cik:010d}'
        url = self.SUBMISSIONS_URL.format(cik=cik10)
        try:
            data = self.client._get_json(url)
        except SECClientError:
            raise
        self._validate_submissions_root(data)
        self._validate_recent_filings(data['filings'])
        return data

    def get_submissions_by_ticker(self, ticker: str) -> Dict[str, Any]:
        if not isinstance(ticker, str) or not ticker.strip():
            raise SECConfigurationError('Ticker must be a non-empty string.')
        try:
            cik = self.client.get_cik_by_ticker(ticker)
        except SECTickerNotFoundError:
            raise
        except SECClientError:
            raise
        return self.get_submissions_by_cik(cik)

    def get_recent_filings_by_cik(self, cik: int) -> List[SECFiling]:
        data = self.get_submissions_by_cik(cik)
        recent = data['filings']['recent']
        cik_value = int(data['cik'])
        forms = recent['form']
        accessions = recent['accessionNumber']
        filing_dates = recent['filingDate']
        report_dates = recent['reportDate']
        primary_documents = recent['primaryDocument']
        lengths = {len(forms), len(accessions), len(filing_dates), len(report_dates), len(primary_documents)}
        if len(lengths) != 1:
            raise SECSubmissionsDataError('SEC Submissions recent filing arrays have inconsistent lengths.')
        file_numbers = recent.get('fileNumber')
        if file_numbers is not None and (not isinstance(file_numbers, list)):
            raise SECSubmissionsDataError("SEC Submissions 'fileNumber' must be a list.")
        result: List[SECFiling] = []
        for index in range(len(forms)):
            form = forms[index]
            accession = accessions[index]
            filing_date = filing_dates[index]
            report_date = report_dates[index]
            primary_document = primary_documents[index]
            if not isinstance(form, str):
                raise SECSubmissionsDataError(f'Invalid form at filing index {index}.')
            if not isinstance(accession, str):
                raise SECSubmissionsDataError(f'Invalid accession number at filing index {index}.')
            if not isinstance(filing_date, str):
                raise SECSubmissionsDataError(f'Invalid filing date at filing index {index}.')
            if report_date is not None and (not isinstance(report_date, str)):
                raise SECSubmissionsDataError(f'Invalid report date at filing index {index}.')
            if primary_document is not None and (not isinstance(primary_document, str)):
                raise SECSubmissionsDataError(f'Invalid primary document at filing index {index}.')
            file_number = None
            if file_numbers is not None:
                file_number = file_numbers[index]
            is_amendment = form.endswith('/A')
            filing_url = ''
            if primary_document:
                filing_url = self._build_filing_url(cik=cik_value, accession_number=accession, primary_document=primary_document)
            result.append(SECFiling(cik=cik_value, form=form, accession_number=accession, filing_date=filing_date, report_date=report_date, primary_document=primary_document, is_amendment=is_amendment, file_number=file_number, filing_url=filing_url))
        return result

    def get_recent_filings_by_ticker(self, ticker: str) -> List[SECFiling]:
        cik = self.client.get_cik_by_ticker(ticker)
        return self.get_recent_filings_by_cik(cik)