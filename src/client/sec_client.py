from __future__ import annotations
import threading
import time
from typing import Any, Dict, Optional
import requests

class SECClientError(Exception):
    pass

class SECConfigurationError(SECClientError):
    pass

class SECRequestError(SECClientError):
    pass

class SECDataError(SECClientError):
    pass

class SECTickerNotFoundError(SECClientError):
    pass

class SECClient:
    BASE = 'https://data.sec.gov'
    CIK_LOOKUP = 'https://www.sec.gov/files/company_tickers.json'
    COMPANY_FACTS = BASE + '/api/xbrl/companyfacts/CIK{cik}.json'
    DEFAULT_REQUESTS_PER_SECOND = 8.0
    DEFAULT_TIMEOUT = 10.0

    def __init__(self, name: str, email: str, organization: str='', requests_per_second: float=DEFAULT_REQUESTS_PER_SECOND, timeout: float=DEFAULT_TIMEOUT, session: Optional[requests.Session]=None) -> None:
        if not isinstance(name, str) or not name.strip():
            raise SECConfigurationError('SEC User-Agent requires a non-empty name.')
        if not isinstance(email, str) or not email.strip():
            raise SECConfigurationError('SEC User-Agent requires a non-empty email.')
        if not isinstance(requests_per_second, (int, float)):
            raise SECConfigurationError('requests_per_second must be numeric.')
        if requests_per_second <= 0:
            raise SECConfigurationError('requests_per_second must be greater than zero.')
        if requests_per_second > 10:
            raise SECConfigurationError('requests_per_second must not exceed 10 requests/sec.')
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise SECConfigurationError('timeout must be a positive number.')
        name = name.strip()
        email = email.strip()
        organization = organization.strip()
        self.user_agent = f'{name} ({email})' if not organization else f'{name} ({email}); {organization}'
        self.session = session or requests.Session()
        self.session.headers.update({'User-Agent': self.user_agent, 'Accept-Encoding': 'gzip, deflate', 'Accept': 'application/json'})
        self.timeout = float(timeout)
        self.min_interval = 1.0 / float(requests_per_second)
        self._last_request_time = 0.0
        self._rate_lock = threading.Lock()
        self._ticker_map: Optional[Dict[str, int]] = None
        self._ticker_map_lock = threading.Lock()

    def _rate_limit(self) -> None:
        with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_request_time = time.monotonic()

    def _get_json(self, url: str) -> Dict[str, Any]:
        self._rate_limit()
        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise SECRequestError(f"SEC request failed for URL '{url}': {exc}") from exc
        if response.status_code != 200:
            raise SECRequestError(f"SEC returned HTTP {response.status_code} for URL '{url}'.")
        try:
            data = response.json()
        except ValueError as exc:
            raise SECDataError(f"Malformed JSON returned by SEC for URL '{url}'.") from exc
        if not isinstance(data, dict):
            raise SECDataError(f"Unexpected JSON structure for URL '{url}'; expected a JSON object.")
        return data

    def _get_content(self, url: str) -> tuple[bytes, str]:
        self._rate_limit()
        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise SECRequestError(f"SEC request failed for URL '{url}': {exc}") from exc
        if response.status_code != 200:
            raise SECRequestError(f"SEC returned HTTP {response.status_code} for URL '{url}'.")
        if not response.content:
            raise SECDataError(f"SEC returned empty content for URL '{url}'.")
        content_type = response.headers.get('Content-Type', '').strip()
        return (response.content, content_type)

    def _load_ticker_map(self) -> Dict[str, int]:
        if self._ticker_map is not None:
            return self._ticker_map
        with self._ticker_map_lock:
            if self._ticker_map is not None:
                return self._ticker_map
            data = self._get_json(self.CIK_LOOKUP)
            if not isinstance(data, dict):
                raise SECDataError('Unexpected structure in SEC ticker lookup; expected a JSON object.')
            ticker_map: Dict[str, int] = {}
            for entry in data.values():
                if not isinstance(entry, dict):
                    continue
                ticker = entry.get('ticker')
                cik = entry.get('cik_str')
                if not isinstance(ticker, str):
                    continue
                ticker_normalized = ticker.strip().upper()
                if not ticker_normalized:
                    continue
                if isinstance(cik, int):
                    ticker_map[ticker_normalized] = cik
                elif isinstance(cik, str) and cik.isdigit():
                    ticker_map[ticker_normalized] = int(cik)
            if not ticker_map:
                raise SECDataError('SEC ticker lookup returned no usable ticker/CIK associations.')
            self._ticker_map = ticker_map
            return self._ticker_map

    def get_cik_by_ticker(self, ticker: str) -> int:
        if not isinstance(ticker, str) or not ticker.strip():
            raise SECConfigurationError('Ticker must be a non-empty string.')
        ticker_upper = ticker.strip().upper()
        ticker_map = self._load_ticker_map()
        cik = ticker_map.get(ticker_upper)
        if cik is None:
            raise SECTickerNotFoundError(f"Ticker '{ticker_upper}' was not found in the SEC ticker/CIK mapping.")
        return cik

    def get_company_facts_by_cik(self, cik: int) -> Dict[str, Any]:
        if not isinstance(cik, int) or isinstance(cik, bool):
            raise SECConfigurationError(f'CIK must be an integer; received {cik!r}.')
        if cik < 0:
            raise SECConfigurationError(f'CIK must be non-negative; received {cik}.')
        cik10 = f'{cik:010d}'
        url = self.COMPANY_FACTS.format(cik=cik10)
        data = self._get_json(url)
        facts = data.get('facts')
        if not isinstance(facts, dict):
            raise SECDataError(f"Unexpected CompanyFacts structure for CIK {cik10}; missing dictionary 'facts'.")
        return data

    def get_company_facts_by_ticker(self, ticker: str) -> Dict[str, Any]:
        cik = self.get_cik_by_ticker(ticker)
        return self.get_company_facts_by_cik(cik)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> 'SECClient':
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()