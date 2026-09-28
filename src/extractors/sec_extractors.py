"""
sec_extractors.py

Production-grade helpers for extracting structured data from
SEC EDGAR XBRL CompanyFacts JSON responses.

- Safe navigation of nested JSON
- Clear exceptions for missing taxonomy/tag/unit
- DataFrame output for modeling
"""

from __future__ import annotations

from typing import Any, Dict, List
import pandas as pd


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SECExtractorError(Exception):
    """Base exception for extractor errors."""


class SECStructureError(SECExtractorError):
    """Raised when CompanyFacts JSON structure is malformed."""


class SECTagNotFoundError(SECExtractorError):
    """Raised when taxonomy/tag/unit is missing."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_dict(obj: Any, context: str) -> Dict[str, Any]:
    """
    Ensure object is a dict; raise structural error otherwise.
    """
    if not isinstance(obj, dict):
        raise SECStructureError(f"Expected dict for {context}, got {type(obj).__name__}")
    return obj


# ---------------------------------------------------------------------------
# Public API: listing helpers
# ---------------------------------------------------------------------------

def list_taxonomies(company_facts: Dict[str, Any]) -> List[str]:
    """
    Return list of available taxonomies (e.g., us-gaap, dei).
    """
    root = _ensure_dict(company_facts, "company_facts root")
    facts = _ensure_dict(root.get("facts"), "'facts' section")
    return list(facts.keys())


def list_tags(company_facts: Dict[str, Any], taxonomy: str) -> List[str]:
    """
    Return list of tags under a taxonomy.
    """
    root = _ensure_dict(company_facts, "company_facts root")
    facts = _ensure_dict(root.get("facts"), "'facts' section")

    if taxonomy not in facts:
        raise SECTagNotFoundError(f"Taxonomy '{taxonomy}' not found.")

    taxonomy_section = _ensure_dict(facts[taxonomy], f"taxonomy '{taxonomy}'")
    return list(taxonomy_section.keys())


def list_units_for_tag(company_facts: Dict[str, Any], taxonomy: str, tag: str) -> List[str]:
    """
    Return list of units for a given taxonomy + tag.
    """
    root = _ensure_dict(company_facts, "company_facts root")
    facts = _ensure_dict(root.get("facts"), "'facts' section")

    if taxonomy not in facts:
        raise SECTagNotFoundError(f"Taxonomy '{taxonomy}' not found.")

    taxonomy_section = _ensure_dict(facts[taxonomy], f"taxonomy '{taxonomy}'")

    if tag not in taxonomy_section:
        raise SECTagNotFoundError(f"Tag '{tag}' not found under taxonomy '{taxonomy}'.")

    tag_section = _ensure_dict(taxonomy_section[tag], f"tag '{tag}'")
    units = _ensure_dict(tag_section.get("units"), f"'units' for tag '{tag}'")

    return list(units.keys())


# ---------------------------------------------------------------------------
# Public API: extract time series
# ---------------------------------------------------------------------------

def extract_fact_series(
    company_facts: Dict[str, Any],
    taxonomy: str,
    tag: str,
    unit: str = "USD",
) -> pd.DataFrame:
    """
    Extract a time series for taxonomy + tag + unit as a DataFrame.

    Raises:
        SECTagNotFoundError: taxonomy/tag/unit missing
        SECStructureError: malformed JSON structure
    """
    root = _ensure_dict(company_facts, "company_facts root")
    facts = _ensure_dict(root.get("facts"), "'facts' section")

    if taxonomy not in facts:
        raise SECTagNotFoundError(f"Taxonomy '{taxonomy}' not found.")

    taxonomy_section = _ensure_dict(facts[taxonomy], f"taxonomy '{taxonomy}'")

    if tag not in taxonomy_section:
        raise SECTagNotFoundError(f"Tag '{tag}' not found under taxonomy '{taxonomy}'.")

    tag_section = _ensure_dict(taxonomy_section[tag], f"tag '{tag}'")
    units = _ensure_dict(tag_section.get("units"), f"'units' for tag '{tag}'")

    if unit not in units:
        raise SECTagNotFoundError(
            f"Unit '{unit}' not found for tag '{tag}' under taxonomy '{taxonomy}'."
        )

    series_list = units[unit]

    if not isinstance(series_list, list):
        raise SECStructureError(
            f"Expected list of observations for tag '{tag}' unit '{unit}', "
            f"got {type(series_list).__name__}"
        )

    # Convert list of dicts → DataFrame
    df = pd.DataFrame(series_list)

    # Parse dates safely
    if "end" in df.columns:
        df["end"] = pd.to_datetime(df["end"], errors="coerce")
    if "start" in df.columns:
        df["start"] = pd.to_datetime(df["start"], errors="coerce")

    # Sort by end date if present
    if "end" in df.columns:
        df = df.sort_values("end")

    return df.reset_index(drop=True)
