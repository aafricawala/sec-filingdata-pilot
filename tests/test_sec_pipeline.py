from typing import Any, Dict
import pandas as pd
import pytest
import pipeline.sec_pipeline as sec_pipeline
from pipeline.sec_pipeline import SECPipelineError, extract_metrics_for_ticker

class DummySECClient:

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get_company_facts_by_ticker(self, ticker: str) -> Dict[str, Any]:
        return {'facts': {'us-gaap': {'Revenues': {'units': {'USD': [{'end': '2023-12-31', 'val': 100.0}, {'end': '2022-12-31', 'val': 90.0}]}}, 'NetIncomeLoss': {'units': {'USD': [{'end': '2023-12-31', 'val': 50.0}]}}}, 'dei': {'EntityCommonStockSharesOutstanding': {'units': {'shares': [{'end': '2023-12-31', 'val': 1000000}]}}}}}

    def close(self) -> None:
        pass

def test_pipeline_runs_with_dummy_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sec_pipeline, 'SECClient', DummySECClient)
    out = extract_metrics_for_ticker(ticker='MSFT', name='TestName', email='test@example.com', organization='TestOrg')
    assert isinstance(out, dict)
    assert 'income_statement' in out
    assert 'balance_sheet' in out
    assert 'cash_flow' in out
    assert 'etf' in out
    income = out['income_statement']
    assert 'Revenues' in income
    assert 'NetIncomeLoss' in income
    revenues_df = income['Revenues']
    assert isinstance(revenues_df, pd.DataFrame)
    assert 'val' in revenues_df.columns
    assert len(revenues_df) == 2

def test_pipeline_handles_missing_tags_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:

    class DummySECClientMissing:

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def get_company_facts_by_ticker(self, ticker: str) -> Dict[str, Any]:
            return {'facts': {'us-gaap': {'Revenues': {'units': {'USD': [{'end': '2023-12-31', 'val': 100.0}]}}}}}

        def close(self) -> None:
            pass
    monkeypatch.setattr(sec_pipeline, 'SECClient', DummySECClientMissing)
    out = extract_metrics_for_ticker(ticker='AAPL', name='TestName', email='test@example.com', organization='TestOrg')
    income = out['income_statement']
    assert 'Revenues' in income
    assert 'NetIncomeLoss' not in income or isinstance(income.get('NetIncomeLoss'), pd.DataFrame)

def test_pipeline_propagates_structure_errors(monkeypatch: pytest.MonkeyPatch) -> None:

    class DummySECClientBadStructure:

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def get_company_facts_by_ticker(self, ticker: str) -> Dict[str, Any]:
            return {'facts': ['not', 'a', 'dict']}

        def close(self) -> None:
            pass
    monkeypatch.setattr(sec_pipeline, 'SECClient', DummySECClientBadStructure)
    with pytest.raises(SECPipelineError):
        extract_metrics_for_ticker(ticker='MSFT', name='TestName', email='test@example.com', organization='TestOrg')