"""
sec_pipeline.py

High-level SEC ingestion and metric extraction pipeline.

Responsibilities:
- Accept a ticker and SEC identity information.
- Delegate ticker -> CIK resolution to SECClient.
- Delegate SEC HTTP retrieval to SECClient.
- Fetch raw CompanyFacts data.
- Pass CompanyFacts to XBRL extractors.
- Iterate configured metric groups and metric-specific units.
- Gracefully omit unavailable metrics.
- Escalate malformed SEC structures and SEC client failures.

This module does not perform HTTP requests directly and does not interpret
financial meaning beyond the configured extraction rules.

The pipeline is intentionally deterministic:
- SECClient owns network access and SEC identity/rate-limiting behavior.
- sec_extractors owns XBRL structure validation and fact extraction.
- sec_metrics owns the configured metric taxonomy, tags, and units.
- This module orchestrates those components only.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sec_client import (
    SECClient,
    SECClientError,
    SECConfigurationError,
    SECDataError,
    SECRequestError,
    SECTickerNotFoundError,
)

from sec_extractors import (
    SECStructureError,
    SECTagNotFoundError,
    extract_fact_series,
)

from sec_metrics import ALL_METRIC_GROUPS


class SECPipelineError(Exception):
    """Base exception for SEC pipeline failures."""


def extract_metrics_for_ticker(
    ticker: str,
    name: str,
    email: str,
    organization: str = "",
    client: Optional[SECClient] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Fetch SEC CompanyFacts for a ticker and extract all configured metrics.

    Args:
        ticker:
            Stock ticker symbol, e.g. "MSFT".

        name:
            Human-readable identity used in the SEC User-Agent.

        email:
            Contact email used in the SEC User-Agent.

        organization:
            Optional organization/project name used in the SEC User-Agent.

        client:
            Optional reusable SECClient instance.

    Returns:
        Dictionary containing extracted metric DataFrames grouped by
        metric category.

    Raises:
        SECPipelineError:
            If SEC retrieval or extraction fails in a way that should
            terminate the pipeline.
    """

    if not isinstance(ticker, str) or not ticker.strip():
        raise SECPipelineError("Ticker must be a non-empty string.")

    ticker_normalized = ticker.strip().upper()

    owned_client = False

    if client is None:
        try:
            client = SECClient(
                name=name,
                email=email,
                organization=organization,
            )
        except SECClientError as exc:
            raise SECPipelineError(
                f"Failed to initialize SEC client: {exc}"
            ) from exc

        owned_client = True

    try:
        # ---------------------------------------------------------------
        # Retrieve raw SEC CompanyFacts
        # ---------------------------------------------------------------

        try:
            company_facts = client.get_company_facts_by_ticker(
                ticker_normalized
            )

        except SECTickerNotFoundError as exc:
            raise SECPipelineError(
                f"Ticker '{ticker_normalized}' was not found in "
                "the SEC ticker/CIK mapping."
            ) from exc

        except (
            SECRequestError,
            SECDataError,
            SECConfigurationError,
        ) as exc:
            raise SECPipelineError(
                f"SEC data retrieval failed for ticker "
                f"'{ticker_normalized}': {exc}"
            ) from exc

        except SECClientError as exc:
            raise SECPipelineError(
                f"SEC client error for ticker "
                f"'{ticker_normalized}': {exc}"
            ) from exc

        # ---------------------------------------------------------------
        # Extract configured metric groups
        # ---------------------------------------------------------------

        results: Dict[str, Dict[str, Any]] = {}

        for group_name, group in ALL_METRIC_GROUPS.items():

            group_results: Dict[str, Any] = {}

            for taxonomy, metrics in group.items():

                for tag, metric_spec in metrics.items():

                    try:
                        df = extract_fact_series(
                            company_facts,
                            taxonomy,
                            tag,
                            unit=metric_spec["unit"],
                        )

                        group_results[tag] = df

                    except SECTagNotFoundError:
                        # Metric is not available for this issuer.
                        # This is expected and is not a pipeline failure.
                        continue

                    except SECStructureError as exc:
                        raise SECPipelineError(
                            f"Malformed CompanyFacts structure while "
                            f"extracting '{taxonomy}:{tag}' for ticker "
                            f"'{ticker_normalized}': {exc}"
                        ) from exc

            # IMPORTANT:
            # This must remain INSIDE the group loop.
            results[group_name] = group_results

        return results

    finally:
        if owned_client and client is not None:
            client.close()


# Backward-compatible alternate name.
build_sec_pipeline = extract_metrics_for_ticker