from __future__ import annotations
from dataclasses import dataclass
import re
from typing import Final, Optional
from models.sec_filings import SECFilingDocument

_ITEM_HEADING_PATTERN: Final[re.Pattern] = re.compile(
    r"^[ \t]*ITEM\s+"
    r"(?P<number>\d+(?:\.\d+)?)"
    r"(?P<letter>[A-Z])?"
    r"(?:\s*[.:)\-–—]?\s*)"
    r"(?P<title>.*?)"
    r"\s*$",
    re.IGNORECASE,
)

_PART_HEADING_PATTERN: Final[re.Pattern] = re.compile(
    r"^[ \t]*PART\s+(?P<part>[IVX]+)"
    r"(?:\s*[.:)\-–—]?\s*)"
    r"(?P<title>.*?)"
    r"\s*$",
    re.IGNORECASE,
)

_SECTION_TAXONOMY: Final[dict[str, tuple[tuple[str, str, int], ...]]] = {
    "10-K": (
        ("ITEM_1", "Business", 1),
        ("ITEM_1A", "Risk Factors", 1),
        ("ITEM_1B", "Unresolved Staff Comments", 1),
        ("ITEM_1C", "Cybersecurity", 1),
        ("ITEM_2", "Properties", 1),
        ("ITEM_3", "Legal Proceedings", 1),
        ("ITEM_4", "Mine Safety Disclosures", 1),
        ("ITEM_5", "Market for Registrant's Common Equity", 1),
        ("ITEM_6", "Reserved", 1),
        ("ITEM_7", "Management's Discussion and Analysis", 1),
        (
            "ITEM_7A",
            "Quantitative and Qualitative Disclosures About Market Risk",
            1,
        ),
        ("ITEM_8", "Financial Statements and Supplementary Data", 1),
        (
            "ITEM_9",
            "Changes in and Disagreements With Accountants",
            1,
        ),
        ("ITEM_9A", "Controls and Procedures", 1),
        ("ITEM_9B", "Other Information", 1),
        ("ITEM_9C", "Disclosure Regarding Foreign Jurisdictions", 1),
    ),
    "10-Q": (
        ("PART_I_ITEM_1", "Financial Statements", 2),
        ("PART_I_ITEM_2", "Management's Discussion and Analysis", 2),
        (
            "PART_I_ITEM_3",
            "Quantitative and Qualitative Disclosures About Market Risk",
            2,
        ),
        ("PART_I_ITEM_4", "Controls and Procedures", 2),
        ("PART_II_ITEM_1", "Legal Proceedings", 2),
        ("PART_II_ITEM_1A", "Risk Factors", 2),
        ("PART_II_ITEM_2", "Unregistered Sales of Equity Securities", 2),
        ("PART_II_ITEM_3", "Defaults Upon Senior Securities", 2),
        ("PART_II_ITEM_4", "Mine Safety Disclosures", 2),
        ("PART_II_ITEM_5", "Other Information", 2),
        ("PART_II_ITEM_6", "Exhibits", 2),
    ),
    "8-K": (
        ("ITEM_1_01", "Entry into a Material Definitive Agreement", 1),
        ("ITEM_1_02", "Termination of a Material Definitive Agreement", 1),
        ("ITEM_1_03", "Bankruptcy or Receivership", 1),
        ("ITEM_1_04", "Mine Safety Reporting", 1),
        ("ITEM_1_05", "Material Cybersecurity Incidents", 1),
        ("ITEM_2_01", "Completion of Acquisition or Disposition of Assets", 1),
        ("ITEM_2_02", "Results of Operations and Financial Condition", 1),
        ("ITEM_2_03", "Creation of a Direct Financial Obligation", 1),
        (
            "ITEM_2_04",
            "Triggering Events That Accelerate or Increase "
            "a Direct Financial Obligation",
            1,
        ),
        ("ITEM_2_05", "Costs Associated With Exit or Disposal Activities", 1),
        ("ITEM_2_06", "Material Impairments", 1),
        (
            "ITEM_3_01",
            "Notice of Delisting or Failure to Satisfy "
            "a Continued Listing Rule",
            1,
        ),
        ("ITEM_3_02", "Unregistered Sales of Equity Securities", 1),
        (
            "ITEM_3_03",
            "Material Modification to Rights of Security Holders",
            1,
        ),
        (
            "ITEM_4_01",
            "Changes in Registrant's Certifying Accountant",
            1,
        ),
        (
            "ITEM_4_02",
            "Non-Reliance on Previously Issued Financial Statements",
            1,
        ),
        ("ITEM_5_01", "Changes in Control of Registrant", 1),
        ("ITEM_5_02", "Departure of Directors or Certain Officers", 1),
        ("ITEM_5_03", "Amendments to Articles of Incorporation or Bylaws", 1),
        ("ITEM_5_04", "Temporary Suspension of Trading", 1),
        ("ITEM_5_05", "Amendment to Registrant's Code of Ethics", 1),
        ("ITEM_5_06", "Change in Shell Company Status", 1),
        ("ITEM_5_07", "Submission of Matters to a Vote", 1),
        ("ITEM_5_08", "Shareholder Director Nominations", 1),
        ("ITEM_6_01", "ABS Informational and Computational Material", 1),
        ("ITEM_6_02", "Change of Servicer or Trustee", 1),
        ("ITEM_6_03", "Change in Credit Enhancement", 1),
        ("ITEM_6_04", "Failure to Make a Required Distribution", 1),
        ("ITEM_6_05", "Securities Act Updating Disclosure", 1),
        ("ITEM_7_01", "Regulation FD Disclosure", 1),
        ("ITEM_8_01", "Other Events", 1),
        ("ITEM_9_01", "Financial Statements and Exhibits", 1),
    ),
}

@dataclass(frozen=True)
class _HeadingCandidate:
    """Internal immutable representation of a recognized filing heading."""

    item_number: str
    title: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class _TaxonomyEntry:
    """Internal immutable filing taxonomy entry."""

    section_id: str
    title: str
    level: int


def _normalize_title(title: str) -> str:
    """Normalize heading text for deterministic taxonomy matching."""
    normalized = re.sub(r"\s+", " ", title.strip())
    normalized = normalized.strip(" .:-–—\t")
    return normalized.casefold()


def _build_taxonomy(base_form: str) -> dict[str, _TaxonomyEntry]:
    """Build the filing-specific deterministic taxonomy lookup."""
    return {
        section_id: _TaxonomyEntry(
            section_id=section_id,
            title=title,
            level=level,
        )
        for section_id, title, level in _SECTION_TAXONOMY[base_form]
    }


def _taxonomy_key(
    base_form: str,
    item_number: str,
    title: str,
) -> str | None:
    """Resolve an Item heading to the deterministic filing taxonomy."""

    def _heading_key(value: str) -> str:
        """
        Normalize a heading to alphanumeric characters only.

        This intentionally avoids dependence on whitespace, punctuation,
        Unicode apostrophes, or SEC-generated heading fragments.
        """
        return re.sub(
            r"[^a-z0-9]+",
            "",
            value.lower(),
        )

    if base_form == "10-K":
        return f"ITEM_{item_number}"

    if base_form == "8-K":
        return f"ITEM_{item_number.replace('.', '_')}"

    if base_form == "10-Q":
        part_i_titles = {
            "1": "Financial Statements",
            "2": "Management's Discussion and Analysis",
            "3": (
                "Quantitative and Qualitative "
                "Disclosures About Market Risk"
            ),
            "4": "Controls and Procedures",
        }

        part_ii_titles = {
            "1": "Legal Proceedings",
            "1A": "Risk Factors",
            "2": "Unregistered Sales of Equity Securities",
            "3": "Defaults Upon Senior Securities",
            "4": "Mine Safety Disclosures",
            "5": "Other Information",
            "6": "Exhibits",
        }

        observed = _heading_key(title)

        if not observed:
            return None

        if item_number in part_i_titles:
            canonical = _heading_key(part_i_titles[item_number])

            if (
                canonical.startswith(observed)
                or observed.startswith(canonical)
            ):
                return f"PART_I_ITEM_{item_number}"

        if item_number in part_ii_titles:
            canonical = _heading_key(part_ii_titles[item_number])

            if (
                canonical.startswith(observed)
                or observed.startswith(canonical)
            ):
                return f"PART_II_ITEM_{item_number}"

        return None

    return None


def _get_base_form(form: str) -> str:
    """Return the base SEC form, removing an amendment suffix."""
    if not isinstance(form, str) or not form.strip():
        raise ValueError("Filing form must be a non-empty string.")

    normalized = form.strip().upper()

    if normalized.endswith("/A"):
        normalized = normalized[:-2]

    return normalized
