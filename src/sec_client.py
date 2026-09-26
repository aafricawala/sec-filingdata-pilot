"""
sec_client.py

Production-grade SEC EDGAR CompanyFacts + CIK lookup client.

Responsibilities:
- SEC-compliant User-Agent header
- Ticker -> CIK resolution
- CompanyFacts retrieval
- Conservative client-side rate limiting
- Persistent HTTP session
- HTTP / JSON / SEC structure validation
- No API keys required
- Pure REST client; no XBRL interpretation

Designed for:
- Colab
- Local Python
- CI/CD
- Cloud VMs
- Reusable multi-ticker ingestion pipelines
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

import requests


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SECClientError(Exception):
    """Base exception for SEC client errors."""


class SECConfigurationError(SECClientError):
    """Raised when SEC client configuration is invalid."""


class SECRequestError(SECClientError):
    """Raised for HTTP/network-level failures."""


class SECDataError(SECClientError):
    """Raised for malformed JSON or unexpected SEC response structure."""


class SECTickerNotFoundError(SECClientError):
    """Raised when a ticker cannot be resolved to a SEC CIK."""


# ---------------------------------------------------------------------------
# SECClient
# ---------------------------------------------------------------------------

class SECClient:
    """
    REST client for SEC EDGAR public data APIs.

    Responsibilities:
        1. Build a declared SEC User-Agent.
        2. Maintain a persistent HTTP session.
        3. Enforce client-side request spacing.
        4. Resolve ticker -> CIK.
        5. Retrieve CompanyFacts by CIK.
        6. Validate basic SEC response structures.

    This class intentionally does NOT:
        - interpret XBRL concepts
        - select accounting periods
        - normalize financial metrics
        - infer financial meaning
        - calculate investment metrics
    """

    BASE = "https://data.sec.gov"

    CIK_LOOKUP = (
        "https://www.sec.gov/files/company_tickers.json"
    )

    COMPANY_FACTS = (
        BASE + "/api/xbrl/companyfacts/CIK{cik}.json"
    )

    # SEC currently states a maximum of 10 requests/sec.
    # We deliberately default below that ceiling.
    DEFAULT_REQUESTS_PER_SECOND = 8.0

    DEFAULT_TIMEOUT = 10.0

    def __init__(
        self,
        name: str,
        email: str,
        organization: str = "",
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
        timeout: float = DEFAULT_TIMEOUT,
        session: Optional[requests.Session] = None,
    ) -> None:
        """
        Args:
            name:
                Human-readable identity for the SEC User-Agent.

            email:
                Contact email included in the SEC User-Agent.

            organization:
                Optional organization/project name.

            requests_per_second:
                Client-side request rate limit.

                Defaults to 8 requests/sec, intentionally below
                the SEC's currently documented 10 requests/sec ceiling.

            timeout:
                HTTP request timeout in seconds.

            session:
                Optional requests.Session. Supplying one allows
                connection reuse and dependency injection for testing.
        """

        # ---------------------------------------------------------------
        # Validate identity
        # ---------------------------------------------------------------

        if not isinstance(name, str) or not name.strip():
            raise SECConfigurationError(
                "SEC User-Agent requires a non-empty name."
            )

        if not isinstance(email, str) or not email.strip():
            raise SECConfigurationError(
                "SEC User-Agent requires a non-empty email."
            )

        # ---------------------------------------------------------------
        # Validate rate limit
        # ---------------------------------------------------------------

        if not isinstance(requests_per_second, (int, float)):
            raise SECConfigurationError(
                "requests_per_second must be numeric."
            )

        if requests_per_second <= 0:
            raise SECConfigurationError(
                "requests_per_second must be greater than zero."
            )

        if requests_per_second > 10:
            raise SECConfigurationError(
                "requests_per_second must not exceed 10 requests/sec."
            )

        # ---------------------------------------------------------------
        # Validate timeout
        # ---------------------------------------------------------------

        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise SECConfigurationError(
                "timeout must be a positive number."
            )

        # ---------------------------------------------------------------
        # Build User-Agent
        # ---------------------------------------------------------------

        name = name.strip()
        email = email.strip()
        organization = organization.strip()

        self.user_agent = (
            f"{name} ({email})"
            if not organization
            else f"{name} ({email}); {organization}"
        )

        # ---------------------------------------------------------------
        # HTTP session
        # ---------------------------------------------------------------

        self.session = session or requests.Session()

        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json",
            }
        )

        self.timeout = float(timeout)

        # ---------------------------------------------------------------
        # Rate limiter
        # ---------------------------------------------------------------

        self.min_interval = 1.0 / float(requests_per_second)

        self._last_request_time = 0.0

        # Protect rate limiter when multiple threads use the same client.
        self._rate_lock = threading.Lock()

        # ---------------------------------------------------------------
        # Ticker -> CIK cache
        # ---------------------------------------------------------------

        self._ticker_map: Optional[Dict[str, int]] = None

        self._ticker_map_lock = threading.Lock()

    # -------------------------------------------------------------------
    # Internal rate limiter
    # -------------------------------------------------------------------

    def _rate_limit(self) -> None:
        """
        Ensure minimum spacing between requests made through this client.

        Thread-safe for concurrent use of the same SECClient instance.
        """

        with self._rate_lock:
            now = time.monotonic()

            elapsed = now - self._last_request_time

            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)

            self._last_request_time = time.monotonic()

    # -------------------------------------------------------------------
    # Internal GET helper
    # -------------------------------------------------------------------

    def _get_json(self, url: str) -> Dict[str, Any]:
        """
        Perform a GET request and return parsed JSON.

        Raises:
            SECRequestError:
                Network failure or non-success HTTP status.

            SECDataError:
                Malformed JSON or unexpected top-level structure.
        """

        self._rate_limit()

        try:
            response = self.session.get(
                url,
                timeout=self.timeout,
            )

        except requests.RequestException as exc:
            raise SECRequestError(
                f"SEC request failed for URL '{url}': {exc}"
            ) from exc

        if response.status_code != 200:
            raise SECRequestError(
                "SEC returned HTTP "
                f"{response.status_code} for URL '{url}'."
            )

        try:
            data = response.json()

        except ValueError as exc:
            raise SECDataError(
                f"Malformed JSON returned by SEC for URL '{url}'."
            ) from exc

        if not isinstance(data, dict):
            raise SECDataError(
                "Unexpected JSON structure for URL "
                f"'{url}'; expected a JSON object."
            )

        return data

        # -------------------------------------------------------------------
    # Internal raw-content GET helper
    # -------------------------------------------------------------------

    def _get_content(self, url: str) -> tuple[bytes, str]:
        """
        Perform a GET request and return the raw response content.

        This method is intended for SEC resources that are not JSON,
        such as filing HTML/text documents.

        Args:
            url:
                Fully qualified SEC resource URL.

        Returns:
            Tuple containing:
                - Raw response bytes.
                - Response Content-Type header.

        Raises:
            SECRequestError:
                Network failure or non-success HTTP status.
            SECDataError:
                Empty response content.
        """

        self._rate_limit()

        try:
            response = self.session.get(
                url,
                timeout=self.timeout,
            )

        except requests.RequestException as exc:
            raise SECRequestError(
                f"SEC request failed for URL '{url}': {exc}"
            ) from exc

        if response.status_code != 200:
            raise SECRequestError(
                "SEC returned HTTP "
                f"{response.status_code} for URL '{url}'."
            )

        if not response.content:
            raise SECDataError(
                f"SEC returned empty content for URL '{url}'."
            )

        content_type = response.headers.get(
            "Content-Type",
            "",
        ).strip()

        return response.content, content_type

    # -------------------------------------------------------------------
    # Ticker map loading
    # -------------------------------------------------------------------

    def _load_ticker_map(self) -> Dict[str, int]:
        """
        Download and cache SEC ticker -> CIK associations.

        SEC's company_tickers.json is a JSON object whose values
        contain ticker, CIK, and company name information.

        The downloaded mapping is cached for the lifetime of this
        SECClient instance.
        """

        # Fast path: already cached.
        if self._ticker_map is not None:
            return self._ticker_map

        # Prevent multiple concurrent downloads.
        with self._ticker_map_lock:

            # Another thread may have populated it while waiting.
            if self._ticker_map is not None:
                return self._ticker_map

            data = self._get_json(self.CIK_LOOKUP)

            if not isinstance(data, dict):
                raise SECDataError(
                    "Unexpected structure in SEC ticker lookup; "
                    "expected a JSON object."
                )

            ticker_map: Dict[str, int] = {}

            for entry in data.values():

                if not isinstance(entry, dict):
                    continue

                ticker = entry.get("ticker")
                cik = entry.get("cik_str")

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
                raise SECDataError(
                    "SEC ticker lookup returned no usable "
                    "ticker/CIK associations."
                )

            self._ticker_map = ticker_map

            return self._ticker_map

    # -------------------------------------------------------------------
    # Public API: CIK lookup
    # -------------------------------------------------------------------

    def get_cik_by_ticker(self, ticker: str) -> int:
        """
        Resolve a ticker symbol to its SEC CIK.

        Args:
            ticker:
                Stock ticker, e.g. "MSFT".

        Returns:
            Integer SEC CIK.

        Raises:
            SECConfigurationError:
                Invalid ticker input.

            SECTickerNotFoundError:
                Ticker is not present in SEC ticker mapping.

            SECDataError:
                SEC ticker mapping is malformed.
        """

        if not isinstance(ticker, str) or not ticker.strip():
            raise SECConfigurationError(
                "Ticker must be a non-empty string."
            )

        ticker_upper = ticker.strip().upper()

        ticker_map = self._load_ticker_map()

        cik = ticker_map.get(ticker_upper)

        if cik is None:
            raise SECTickerNotFoundError(
                f"Ticker '{ticker_upper}' was not found "
                "in the SEC ticker/CIK mapping."
            )

        return cik

    # -------------------------------------------------------------------
    # Public API: CompanyFacts retrieval
    # -------------------------------------------------------------------

    def get_company_facts_by_cik(
        self,
        cik: int,
    ) -> Dict[str, Any]:
        """
        Fetch SEC CompanyFacts JSON for a given CIK.

        Args:
            cik:
                Integer SEC CIK.

        Returns:
            Raw CompanyFacts JSON object.

        Raises:
            SECConfigurationError:
                Invalid CIK.

            SECRequestError:
                HTTP/network failure.

            SECDataError:
                Malformed or unexpected CompanyFacts structure.
        """

        if not isinstance(cik, int) or isinstance(cik, bool):
            raise SECConfigurationError(
                f"CIK must be an integer; received {cik!r}."
            )

        if cik < 0:
            raise SECConfigurationError(
                f"CIK must be non-negative; received {cik}."
            )

        # SEC CompanyFacts endpoint expects 10-digit zero-padded CIK.
        cik10 = f"{cik:010d}"

        url = self.COMPANY_FACTS.format(cik=cik10)

        data = self._get_json(url)

        # Validate minimum expected CompanyFacts structure.
        facts = data.get("facts")

        if not isinstance(facts, dict):
            raise SECDataError(
                "Unexpected CompanyFacts structure for "
                f"CIK {cik10}; missing dictionary 'facts'."
            )

        return data

    # -------------------------------------------------------------------
    # Public API: CompanyFacts by ticker
    # -------------------------------------------------------------------

    def get_company_facts_by_ticker(
        self,
        ticker: str,
    ) -> Dict[str, Any]:
        """
        Resolve ticker -> CIK and fetch CompanyFacts.
        """

        cik = self.get_cik_by_ticker(ticker)

        return self.get_company_facts_by_cik(cik)

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP session."""

        self.session.close()

    def __enter__(self) -> "SECClient":
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.close()