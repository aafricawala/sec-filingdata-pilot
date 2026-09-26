# tests/test_sec_extractors.py
"""
Unit tests for sec_extractors.py.

Focus areas:
- Correct listing of taxonomies, tags, and units.
- Robust handling of missing or malformed structures.
- Proper DataFrame construction and date parsing in extract_fact_series.

These tests operate purely on in-memory JSON-like dicts and do not
perform any network I/O.
"""

from typing import Any, Dict

import pandas as pd
import pytest

from sec_extractors import (
    SECExtractorError,
    SECStructureError,
    SECTagNotFoundError,
    list_taxonomies,
    list_tags,
    list_units_for_tag,
    extract_fact_series,
)


def _minimal_company_facts() -> Dict[str, Any]:
    """
    Build a minimal but structurally valid CompanyFacts-like dict
    for testing purposes.
    """
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "end": "2023-12-31",
                                "start": "2023-01-01",
                                "val": 100.0,
                                "form": "10-K",
                                "fy": 2023,
                                "fp": "FY",
                            },
                            {
                                "end": "2022-12-31",
                                "start": "2022-01-01",
                                "val": 90.0,
                                "form": "10-K",
                                "fy": 2022,
                                "fp": "FY",
                            },
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "end": "2023-12-31",
                                "val": 50.0,
                                "form": "10-K",
                                "fy": 2023,
                                "fp": "FY",
                            }
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
                                "val": 1000000,
                                "form": "10-K",
                                "fy": 2023,
                                "fp": "FY",
                            }
                        ]
                    }
                }
            },
        }
    }


# ---------------------------------------------------------------------------
# list_taxonomies tests
# ---------------------------------------------------------------------------


def test_list_taxonomies_ok():
    facts = _minimal_company_facts()
    taxonomies = list_taxonomies(facts)
    assert "us-gaap" in taxonomies
    assert "dei" in taxonomies


def test_list_taxonomies_missing_facts_raises():
    facts = {"no_facts_here": {}}
    with pytest.raises(SECStructureError):
        list_taxonomies(facts)


# ---------------------------------------------------------------------------
# list_tags tests
# ---------------------------------------------------------------------------


def test_list_tags_ok():
    facts = _minimal_company_facts()
    tags = list_tags(facts, taxonomy="us-gaap")
    assert "Revenues" in tags
    assert "NetIncomeLoss" in tags


def test_list_tags_missing_taxonomy_raises():
    facts = _minimal_company_facts()
    with pytest.raises(SECTagNotFoundError):
        list_tags(facts, taxonomy="ifrs-full")


def test_list_tags_malformed_taxonomy_section_raises():
    facts = _minimal_company_facts()
    # Corrupt the taxonomy section
    facts["facts"]["us-gaap"] = ["not", "a", "dict"]
    with pytest.raises(SECStructureError):
        list_tags(facts, taxonomy="us-gaap")


# ---------------------------------------------------------------------------
# list_units_for_tag tests
# ---------------------------------------------------------------------------


def test_list_units_for_tag_ok():
    facts = _minimal_company_facts()
    units = list_units_for_tag(facts, taxonomy="us-gaap", tag="Revenues")
    assert "USD" in units


def test_list_units_for_tag_missing_taxonomy_raises():
    facts = _minimal_company_facts()
    with pytest.raises(SECTagNotFoundError):
        list_units_for_tag(facts, taxonomy="ifrs-full", tag="Revenues")


def test_list_units_for_tag_missing_tag_raises():
    facts = _minimal_company_facts()
    with pytest.raises(SECTagNotFoundError):
        list_units_for_tag(facts, taxonomy="us-gaap", tag="NonExistingTag")


def test_list_units_for_tag_malformed_units_raises():
    facts = _minimal_company_facts()
    # Corrupt units structure
    facts["facts"]["us-gaap"]["Revenues"]["units"] = "not-a-dict"
    with pytest.raises(SECStructureError):
        list_units_for_tag(facts, taxonomy="us-gaap", tag="Revenues")


# ---------------------------------------------------------------------------
# extract_fact_series tests
# ---------------------------------------------------------------------------


def test_extract_fact_series_ok():
    facts = _minimal_company_facts()
    df = extract_fact_series(facts, taxonomy="us-gaap", tag="Revenues", unit="USD")

    # Expect two rows
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2

    # Check that 'end' is parsed as datetime and sorted ascending
    assert pd.api.types.is_datetime64_any_dtype(df["end"])
    assert df["end"].iloc[0] < df["end"].iloc[1]

    # Check that 'val' is present
    assert "val" in df.columns
    assert df["val"].iloc[0] == 90.0  # 2022
    assert df["val"].iloc[1] == 100.0  # 2023


def test_extract_fact_series_missing_taxonomy_raises():
    facts = _minimal_company_facts()
    with pytest.raises(SECTagNotFoundError):
        extract_fact_series(facts, taxonomy="ifrs-full", tag="Revenues", unit="USD")


def test_extract_fact_series_missing_tag_raises():
    facts = _minimal_company_facts()
    with pytest.raises(SECTagNotFoundError):
        extract_fact_series(facts, taxonomy="us-gaap", tag="NonExistingTag", unit="USD")


def test_extract_fact_series_missing_unit_raises():
    facts = _minimal_company_facts()
    with pytest.raises(SECTagNotFoundError):
        extract_fact_series(facts, taxonomy="us-gaap", tag="Revenues", unit="shares")


def test_extract_fact_series_malformed_series_list_raises():
    facts = _minimal_company_facts()
    # Corrupt the list of observations
    facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"] = {"not": "a-list"}
    with pytest.raises(SECStructureError):
        extract_fact_series(facts, taxonomy="us-gaap", tag="Revenues", unit="USD")
