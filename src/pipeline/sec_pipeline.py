from __future__ import annotations
from typing import Any, Dict, Optional
from client.sec_client import SECClient, SECClientError, SECConfigurationError, SECDataError, SECRequestError, SECTickerNotFoundError
from extractors.sec_extractors import SECStructureError, SECTagNotFoundError, extract_fact_series
from models.sec_metrics import ALL_METRIC_GROUPS

class SECPipelineError(Exception):
    pass

def extract_metrics_for_ticker(ticker: str, name: str, email: str, organization: str='', client: Optional[SECClient]=None) -> Dict[str, Dict[str, Any]]:
    if not isinstance(ticker, str) or not ticker.strip():
        raise SECPipelineError('Ticker must be a non-empty string.')
    ticker_normalized = ticker.strip().upper()
    owned_client = False
    if client is None:
        try:
            client = SECClient(name=name, email=email, organization=organization)
        except SECClientError as exc:
            raise SECPipelineError(f'Failed to initialize SEC client: {exc}') from exc
        owned_client = True
    try:
        try:
            company_facts = client.get_company_facts_by_ticker(ticker_normalized)
        except SECTickerNotFoundError as exc:
            raise SECPipelineError(f"Ticker '{ticker_normalized}' was not found in the SEC ticker/CIK mapping.") from exc
        except (SECRequestError, SECDataError, SECConfigurationError) as exc:
            raise SECPipelineError(f"SEC data retrieval failed for ticker '{ticker_normalized}': {exc}") from exc
        except SECClientError as exc:
            raise SECPipelineError(f"SEC client error for ticker '{ticker_normalized}': {exc}") from exc
        results: Dict[str, Dict[str, Any]] = {}
        for group_name, group in ALL_METRIC_GROUPS.items():
            group_results: Dict[str, Any] = {}
            for taxonomy, metrics in group.items():
                for tag, metric_spec in metrics.items():
                    try:
                        df = extract_fact_series(company_facts, taxonomy, tag, unit=metric_spec['unit'])
                        group_results[tag] = df
                    except SECTagNotFoundError:
                        continue
                    except SECStructureError as exc:
                        raise SECPipelineError(f"Malformed CompanyFacts structure while extracting '{taxonomy}:{tag}' for ticker '{ticker_normalized}': {exc}") from exc
            results[group_name] = group_results
        return results
    finally:
        if owned_client and client is not None:
            client.close()
build_sec_pipeline = extract_metrics_for_ticker