"""
sec_metrics.py

Defines reusable metric bundles for SEC CompanyFacts extraction.

- No network access.
- Pure configuration.
- Safe to import anywhere.
"""

from typing import Dict, List, TypedDict


class MetricSpec(TypedDict):
    unit: str


# Income Statement metrics
INCOME_STATEMENT: Dict[str, Dict[str, MetricSpec]] = {
    "us-gaap": {
        "Revenues": {"unit": "USD"},
        "CostOfRevenue": {"unit": "USD"},
        "GrossProfit": {"unit": "USD"},
        "OperatingExpenses": {"unit": "USD"},
        "OperatingIncomeLoss": {"unit": "USD"},
        "NetIncomeLoss": {"unit": "USD"},
        "EarningsPerShareBasic": {"unit": "USD/shares"},
        "EarningsPerShareDiluted": {"unit": "USD/shares"},
    }
}

# Balance Sheet metrics
BALANCE_SHEET: Dict[str, Dict[str, MetricSpec]] = {
    "us-gaap": {
        "Assets": {"unit": "USD"},
        "Liabilities": {"unit": "USD"},
        "StockholdersEquity": {"unit": "USD"},
        "CashAndCashEquivalentsAtCarryingValue": {"unit": "USD"},
        "LongTermDebt": {"unit": "USD"},
    }
}

# Cash Flow metrics
CASH_FLOW: Dict[str, Dict[str, MetricSpec]] = {
    "us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"unit": "USD"},
        "NetCashProvidedByUsedInInvestingActivities": {"unit": "USD"},
        "NetCashProvidedByUsedInFinancingActivities": {"unit": "USD"},
    }
}

# Shares / DEI metrics
ETF_METRICS: Dict[str, Dict[str, MetricSpec]] = {
    "dei": {
        "EntityCommonStockSharesOutstanding": {"unit": "shares"},
    }
}

ALL_METRIC_GROUPS = {
    "income_statement": INCOME_STATEMENT,
    "balance_sheet": BALANCE_SHEET,
    "cash_flow": CASH_FLOW,
    "etf": ETF_METRICS,
}