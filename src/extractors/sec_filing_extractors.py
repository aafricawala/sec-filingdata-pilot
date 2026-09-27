"""
sec_filing_extractors.py

Deterministic SEC filing section extraction.
"""

from extractors.sec_extraction_core import (
    SECFilingExtractionError,
    SECFilingFormatError,
    SECFilingSectionError,
    SECFilingSection,
    extract_sections,
)

# Re-exports for tests
from extractors.sec_html_scanner import _scan_raw_html, _RawHTMLToken
from extractors.sec_html_parser import _build_raw_html_elements, _RawHTMLElement
from extractors.sec_heading_models import _HeadingCandidate, _taxonomy_key, _build_taxonomy
from extractors.sec_html_headings import _extract_heading_candidates_html
