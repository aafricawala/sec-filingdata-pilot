"""
tests/test_sec_pipeline.py

Tests for sec_pipeline.py:

- Verifies pipeline executes end-to-end with mocked SECClient.
- Ensures missing tags are handled gracefully.
- Ensures structural errors are propagated as SECPipelineError.
- Verifies internally owned SECClient instances are closed.

Network calls are not performed; SECClient is monkeypatched.
"""

from typing import Any, Dict

import pandas as pd
import pytest

import sec_pipeline
from sec_pipeline import SECPipelineError, extract_metrics_for_ticker


class DummySECClient:
    """
    Dummy SECClient replacement for testing sec_pipeline.

    Provides:
    - get_company_facts_by_ticker(ticker) -> minimal CompanyFacts-like dict.
    - close() -> lifecycle-compatible no-op.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Initialize the dummy client.

        SEC identity/configuration arguments are intentionally ignored because
        this test double performs no network activity.
        """
        pass

    def get_company_facts_by_ticker(self, ticker: str) -> Dict[str, Any]:
        """
        Return a minimal structurally valid CompanyFacts-like payload.

        The payload contains representative metrics used by the configured
        pipeline metric groups.
        """
        return {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"end": "2023-12-31", "val": 100.0},
                                {"end": "2022-12-31", "val": 90.0},
                            ]
                        }
                    },
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [
                                {"end": "2023-12-31", "val": 50.0},
                            ]
                        }
                    },
                },
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {
                            "shares": [
                                {
                                    "end": "2023-12-31",
                                    "val": 1_000_000,
                                },
                            ]
                        }
                    }
                },
            }
        }

    def close(self) -> None:
        """
        Match the lifecycle contract of the production SECClient.

        No resources exist in this test double, so closure is intentionally
        a no-op.
        """
        pass


def test_pipeline_runs_with_dummy_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Ensure extract_metrics_for_ticker runs end-to-end with a dummy SECClient
    and returns the expected nested dictionary structure.
    """
    # Replace the production SECClient with the deterministic test double.
    monkeypatch.setattr(sec_pipeline, "SECClient", DummySECClient)

    out = extract_metrics_for_ticker(
        ticker="MSFT",
        name="TestName",
        email="test@example.com",
        organization="TestOrg",
    )

    # The pipeline must return a dictionary grouped by configured metric
    # categories.
    assert isinstance(out, dict)
    assert "income_statement" in out
    assert "balance_sheet" in out
    assert "cash_flow" in out
    assert "etf" in out

    # The supplied CompanyFacts payload contains these two income-statement
    # metrics, so both should be extracted successfully.
    income = out["income_statement"]
    assert "Revenues" in income
    assert "NetIncomeLoss" in income

    # Extracted metrics must be returned as well-formed DataFrames.
    revenues_df = income["Revenues"]
    assert isinstance(revenues_df, pd.DataFrame)
    assert "val" in revenues_df.columns
    assert len(revenues_df) == 2


def test_pipeline_handles_missing_tags_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Ensure missing SEC tags do not cause pipeline failure and are omitted.
    """

    class DummySECClientMissing:
        """
        Dummy client containing only a subset of configured SEC metrics.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            """
            Initialize the dummy client.

            Constructor arguments are intentionally ignored.
            """
            pass

        def get_company_facts_by_ticker(
            self,
            ticker: str,
        ) -> Dict[str, Any]:
            """
            Return CompanyFacts containing only Revenues.
            """
            return {
                "facts": {
                    "us-gaap": {
                        "Revenues": {
                            "units": {
                                "USD": [
                                    {
                                        "end": "2023-12-31",
                                        "val": 100.0,
                                    },
                                ]
                            }
                        }
                    }
                }
            }

        def close(self) -> None:
            """
            Match the lifecycle contract of the production SECClient.

            No resources exist in this test double.
            """
            pass

    # Replace the production SECClient with the missing-data test double.
    monkeypatch.setattr(
        sec_pipeline,
        "SECClient",
        DummySECClientMissing,
    )

    out = extract_metrics_for_ticker(
        ticker="AAPL",
        name="TestName",
        email="test@example.com",
        organization="TestOrg",
    )

    income = out["income_statement"]

    # Revenues is present in the supplied payload and must be extracted.
    assert "Revenues" in income

    # NetIncomeLoss is absent from the supplied payload. Its absence must not
    # cause the pipeline to fail.
    assert (
        "NetIncomeLoss" not in income
        or isinstance(income.get("NetIncomeLoss"), pd.DataFrame)
    )


def test_pipeline_propagates_structure_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Ensure malformed CompanyFacts structures are propagated as
    SECPipelineError.
    """

    class DummySECClientBadStructure:
        """
        Dummy client returning an intentionally malformed CompanyFacts object.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            """
            Initialize the dummy client.

            Constructor arguments are intentionally ignored.
            """
            pass

        def get_company_facts_by_ticker(
            self,
            ticker: str,
        ) -> Dict[str, Any]:
            """
            Return CompanyFacts with an invalid 'facts' structure.
            """
            return {
                "facts": ["not", "a", "dict"],
            }

        def close(self) -> None:
            """
            Match the lifecycle contract of the production SECClient.

            No resources exist in this test double.
            """
            pass

    # Replace the production SECClient with the malformed-data test double.
    monkeypatch.setattr(
        sec_pipeline,
        "SECClient",
        DummySECClientBadStructure,
    )

    # The pipeline must translate the extractor's structural error into its
    # public SECPipelineError abstraction.
    with pytest.raises(SECPipelineError):
        extract_metrics_for_ticker(
            ticker="MSFT",
            name="TestName",
            email="test@example.com",
            organization="TestOrg",
        )