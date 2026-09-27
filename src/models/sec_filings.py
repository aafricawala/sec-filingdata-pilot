from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote
from client.sec_client import SECClient, SECClientError, SECConfigurationError
from client.sec_submissions import SECFiling

class SECFilingError(Exception):
    pass

class SECFilingDataError(SECFilingError):
    pass

class SECUnsupportedFilingError(SECFilingError):
    pass

@dataclass(frozen=True)
class SECFilingDocument:
    filing: SECFiling
    document_name: str
    document_kind: str
    source_url: str
    content_type: str
    content_hash: str
    content: bytes

class SECFilingsClient:
    EDGAR_ARCHIVES_BASE = 'https://www.sec.gov/Archives/edgar/data'
    PRIMARY_DOCUMENT_KIND = 'primary'
    COMPLETE_SUBMISSION_KIND = 'complete_submission'
    SUPPORTED_BASE_FORMS = frozenset({'10-K', '10-Q', '8-K'})
    ACCESSION_PATTERN = re.compile('^\\d{10}-\\d{2}-\\d{6}$')

    def __init__(self, client: SECClient) -> None:
        if not isinstance(client, SECClient):
            raise SECConfigurationError('SECFilingsClient requires a valid SECClient instance.')
        self.client = client

    @classmethod
    def _get_base_form(cls, form: str) -> str:
        if not isinstance(form, str) or not form:
            raise SECFilingDataError('Filing form must be a non-empty string.')
        if form.endswith('/A'):
            return form[:-2]
        return form

    @classmethod
    def _validate_supported_form(cls, form: str) -> None:
        base_form = cls._get_base_form(form)
        if base_form not in cls.SUPPORTED_BASE_FORMS:
            raise SECUnsupportedFilingError(f'Unsupported SEC filing form: {form!r}. Supported base forms: {sorted(cls.SUPPORTED_BASE_FORMS)}.')

    @classmethod
    def _validate_filing(cls, filing: SECFiling) -> None:
        if not isinstance(filing, SECFiling):
            raise SECFilingDataError('filing must be an SECFiling instance.')
        if not isinstance(filing.cik, int) or isinstance(filing.cik, bool) or filing.cik < 0:
            raise SECFilingDataError(f'Filing CIK must be a non-negative integer; received {filing.cik!r}.')
        if not isinstance(filing.form, str) or not filing.form:
            raise SECFilingDataError('Filing form must be a non-empty string.')
        cls._validate_supported_form(filing.form)
        if not isinstance(filing.accession_number, str) or not filing.accession_number:
            raise SECFilingDataError('Filing accession number must be a non-empty string.')
        if not cls.ACCESSION_PATTERN.fullmatch(filing.accession_number):
            raise SECFilingDataError(f'Filing accession number has an invalid format: {filing.accession_number!r}.')
        if not isinstance(filing.filing_date, str) or not filing.filing_date:
            raise SECFilingDataError('Filing date must be a non-empty string.')
        if filing.report_date is not None and (not isinstance(filing.report_date, str)):
            raise SECFilingDataError('Filing report date must be a string or None.')
        if filing.primary_document is not None and (not isinstance(filing.primary_document, str)):
            raise SECFilingDataError('Filing primary document must be a string or None.')
        if not isinstance(filing.is_amendment, bool):
            raise SECFilingDataError('Filing is_amendment must be a boolean.')
        if filing.file_number is not None and (not isinstance(filing.file_number, str)):
            raise SECFilingDataError('Filing file number must be a string or None.')
        if not isinstance(filing.filing_url, str):
            raise SECFilingDataError('Filing URL must be a string.')

    @staticmethod
    def _validate_document_name(document_name: str) -> None:
        if not isinstance(document_name, str) or not document_name:
            raise SECFilingDataError('Document name must be a non-empty string.')
        if '\x00' in document_name:
            raise SECFilingDataError('Document name contains a NUL character.')
        if '/' in document_name or '\\' in document_name:
            raise SECFilingDataError('Document name must not contain path separators.')
        if document_name in {'.', '..'}:
            raise SECFilingDataError('Document name must not be a path traversal component.')

    @staticmethod
    def _normalize_accession(accession_number: str) -> str:
        return accession_number.replace('-', '')

    @classmethod
    def _build_primary_document_url(cls, filing: SECFiling) -> str:
        if not filing.primary_document:
            raise SECFilingDataError('Primary document is required for primary-document retrieval.')
        cls._validate_document_name(filing.primary_document)
        accession_path = cls._normalize_accession(filing.accession_number)
        encoded_document_name = quote(filing.primary_document, safe='._-')
        return f'{cls.EDGAR_ARCHIVES_BASE}/{filing.cik}/{accession_path}/{encoded_document_name}'

    @classmethod
    def _build_complete_submission_url(cls, filing: SECFiling) -> str:
        accession_path = cls._normalize_accession(filing.accession_number)
        accession_filename = f'{filing.accession_number}.txt'
        return f'{cls.EDGAR_ARCHIVES_BASE}/{filing.cik}/{accession_path}/{accession_filename}'

    @staticmethod
    def _calculate_content_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _retrieve_document(self, filing: SECFiling, document_name: str, document_kind: str, source_url: str) -> SECFilingDocument:
        try:
            content, content_type = self.client._get_content(source_url)
        except SECClientError:
            raise
        if not isinstance(content, bytes):
            raise SECFilingDataError('SECClient returned filing content that is not bytes.')
        if not content:
            raise SECFilingDataError('SECClient returned empty filing content.')
        if not isinstance(content_type, str):
            raise SECFilingDataError('SECClient returned an invalid content type.')
        content_hash = self._calculate_content_hash(content)
        return SECFilingDocument(filing=filing, document_name=document_name, document_kind=document_kind, source_url=source_url, content_type=content_type, content_hash=content_hash, content=content)

    def get_primary_document(self, filing: SECFiling) -> SECFilingDocument:
        self._validate_filing(filing)
        if not filing.primary_document:
            raise SECFilingDataError('Cannot retrieve primary document because the filing does not specify a primary document.')
        source_url = self._build_primary_document_url(filing)
        return self._retrieve_document(filing=filing, document_name=filing.primary_document, document_kind=self.PRIMARY_DOCUMENT_KIND, source_url=source_url)

    def get_complete_submission(self, filing: SECFiling) -> SECFilingDocument:
        self._validate_filing(filing)
        source_url = self._build_complete_submission_url(filing)
        document_name = f'{filing.accession_number}.txt'
        return self._retrieve_document(filing=filing, document_name=document_name, document_kind=self.COMPLETE_SUBMISSION_KIND, source_url=source_url)