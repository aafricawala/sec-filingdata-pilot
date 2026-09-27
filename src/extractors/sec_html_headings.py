from __future__ import annotations
import re
from typing import Final
from extractors.sec_html_parser import _RawHTMLElement, _build_raw_html_elements, _element_text
from extractors.sec_heading_models import _HeadingCandidate, _normalize_title, _ITEM_HEADING_PATTERN, _PART_HEADING_PATTERN
from extractors.sec_html_scanner import _scan_raw_html

_HTML_BLOCK_ELEMENTS: Final[frozenset[bytes]] = frozenset(
    {
        b"address",
        b"article",
        b"aside",
        b"blockquote",
        b"canvas",
        b"dd",
        b"div",
        b"dl",
        b"dt",
        b"fieldset",
        b"figcaption",
        b"figure",
        b"footer",
        b"form",
        b"h1",
        b"h2",
        b"h3",
        b"h4",
        b"h5",
        b"h6",
        b"header",
        b"hr",
        b"li",
        b"main",
        b"nav",
        b"noscript",
        b"ol",
        b"p",
        b"pre",
        b"section",
        b"table",
        b"td",
        b"tfoot",
        b"th",
        b"tr",
        b"ul",
        b"video",
    }
)

def _is_heading_like_element(tag_name: bytes) -> bool:
    """
    Return whether an element is a plausible filing heading container.

    Standard heading tags are preferred. Common block-level SEC-generated
    containers are also inspected because many filings represent headings
    with div/p/td/span combinations rather than semantic h1-h6 tags.
    """
    return tag_name in _HTML_BLOCK_ELEMENTS or tag_name in {
        b"title",
        b"span",
    }


# ---------------------------------------------------------------------------
# HTML heading candidate filtering
# ---------------------------------------------------------------------------


def _normalize_html_heading_text(text: str) -> str:
    """
    Normalize HTML-derived heading text for structural comparison.

    Unicode whitespace is intentionally normalized because SEC HTML commonly
    uses characters such as U+2009 THIN SPACE between 'Item' and the number.
    """
    return re.sub(r"\s+", " ", text).strip()


def _has_terminal_page_number(text: str) -> bool:
    """Return whether a heading title ends with a standalone page number."""
    normalized = _normalize_html_heading_text(text)

    return bool(re.search(r"\s+\d{1,4}$", normalized))


def _matches_item_heading(text: str) -> bool:
    """Return whether normalized text is a complete SEC Item heading."""
    normalized = _normalize_html_heading_text(text)
    return bool(normalized and _ITEM_HEADING_PATTERN.match(normalized))


def _has_navigation_ancestor(
    elements: tuple[_RawHTMLElement, ...],
    parents: tuple[Optional[int], ...],
    element_index: int,
) -> bool:
    """
    Return whether an element is contained by a likely TOC/navigation tree.

    SEC HTML tables of contents are commonly represented using table
    structures or explicit navigation containers. Actual filing headings are
    structurally outside those containers.
    """
    navigation_tags = {
        b"nav",
        b"table",
        b"thead",
        b"tbody",
        b"tfoot",
        b"tr",
        b"td",
        b"th",
    }

    current_index: Optional[int] = element_index

    while current_index is not None:
        if elements[current_index].tag_name in navigation_tags:
            return True

        current_index = parents[current_index]

    return False


def _has_descendant_complete_item_heading(
    elements: tuple[_RawHTMLElement, ...],
    tokens: tuple[_RawHTMLToken, ...],
    content: bytes,
    element: _RawHTMLElement,
) -> bool:
    """
    Return whether an element contains a complete descendant Item heading.

    This prevents broad containers such as <body> or <div> from being
    interpreted as headings merely because their aggregate text begins with
    an Item heading.

    Fragmentary descendants are intentionally ignored so split headings such
    as 'FINA' + 'NCIAL' can still be recognized from their enclosing element.
    """
    pending = list(element.child_indexes)
    visited: set[int] = set()

    while pending:
        child_index = pending.pop()

        if child_index in visited:
            continue

        visited.add(child_index)

        child = elements[child_index]
        child_text = _normalize_html_heading_text(
            _element_text(content, tokens, child)
        )

        if _matches_item_heading(child_text):
            return True

        pending.extend(child.child_indexes)

    return False


def _build_html_parent_indexes(
    elements: tuple[_RawHTMLElement, ...],
) -> tuple[Optional[int], ...]:
    """
    Build deterministic parent indexes for reconstructed HTML elements.

    The raw element tree stores child relationships only. Parent indexes are
    derived once so heading recognition can climb from nested inline elements
    to their semantic block container.
    """
    parents: list[Optional[int]] = [None] * len(elements)

    for parent_index, element in enumerate(elements):
        for child_index in element.child_indexes:
            if parents[child_index] is None:
                parents[child_index] = parent_index

    return tuple(parents)


def _find_complete_heading_container(
    elements: tuple[_RawHTMLElement, ...],
    parents: tuple[Optional[int], ...],
    tokens: tuple[_RawHTMLToken, ...],
    content: bytes,
    element_index: int,
) -> tuple[int, str] | None:
    """
    Find the nearest heading-like element containing a complete Item heading.

    Real SEC inline-XBRL filings may split one visible heading across several
    nested spans.

    The nearest matching element is authoritative. Once an element itself
    contains a complete Item heading, do not promote it to a broader ancestor,
    because broader ancestors may contain multiple separate Item headings and
    would incorrectly swallow subsequent sections.
    """
    current_index: Optional[int] = element_index

    while current_index is not None:
        element = elements[current_index]

        if not _is_heading_like_element(element.tag_name):
            current_index = parents[current_index]
            continue

        text = _normalize_html_heading_text(
            _element_text(content, tokens, element)
        )

        if len(text) > 500:
            current_index = parents[current_index]
            continue

        if _matches_item_heading(text):
            return current_index, text

        current_index = parents[current_index]

    return None


def _candidate_ranges_overlap(
    left: _HeadingCandidate,
    right: _HeadingCandidate,
) -> bool:
    """Return whether two heading candidates occupy overlapping raw ranges."""
    return (
        left.start_offset < right.end_offset
        and right.start_offset < left.end_offset
    )


def _candidate_contains(
    outer: _HeadingCandidate,
    inner: _HeadingCandidate,
) -> bool:
    """Return whether one candidate raw range contains another."""
    return (
        outer.start_offset <= inner.start_offset
        and outer.end_offset >= inner.end_offset
    )


def _deduplicate_nested_heading_candidates(
    candidates: list[_HeadingCandidate],
) -> tuple[_HeadingCandidate, ...]:
    """
    Remove duplicate or nested HTML representations of the same heading.

    Identical raw ranges are collapsed to one candidate.

    When multiple representations of the same Item overlap, the most
    specific representation is retained. In practice this is the shortest
    raw range, which prevents a parent block/container from duplicating a
    semantic heading.

    Non-overlapping occurrences of the same Item remain distinct.
    """
    if not candidates:
        return ()

    grouped: dict[tuple[str, str], list[_HeadingCandidate]] = {}

    for candidate in candidates:
        identity = (
            candidate.item_number,
            _normalize_title(candidate.title),
        )
        grouped.setdefault(identity, []).append(candidate)

    retained: list[_HeadingCandidate] = []

    for group in grouped.values():
        unique_ranges: dict[
            tuple[int, int],
            _HeadingCandidate,
        ] = {}

        for candidate in group:
            unique_ranges.setdefault(
                (
                    candidate.start_offset,
                    candidate.end_offset,
                ),
                candidate,
            )

        ordered = sorted(
            unique_ranges.values(),
            key=lambda candidate: (
                candidate.start_offset,
                candidate.end_offset,
            ),
        )

        group_retained: list[_HeadingCandidate] = []

        for candidate in ordered:
            overlapping_indexes: list[int] = []

            for index, existing in enumerate(group_retained):
                if _candidate_ranges_overlap(candidate, existing):
                    overlapping_indexes.append(index)

            if not overlapping_indexes:
                group_retained.append(candidate)
                continue

            candidate_length = (
                candidate.end_offset - candidate.start_offset
            )

            shortest_existing_index = min(
                overlapping_indexes,
                key=lambda index: (
                    group_retained[index].end_offset
                    - group_retained[index].start_offset,
                    group_retained[index].start_offset,
                    group_retained[index].end_offset,
                ),
            )

            existing = group_retained[shortest_existing_index]
            existing_length = (
                existing.end_offset - existing.start_offset
            )

            if candidate_length < existing_length:
                group_retained[shortest_existing_index] = candidate

        retained.extend(group_retained)

    return tuple(
        sorted(
            retained,
            key=lambda item: (
                item.start_offset,
                item.end_offset,
                item.item_number,
                _normalize_title(item.title),
            ),
        )
    )


def _extract_heading_candidates_html(
    content: bytes,
    base_form: str,
) -> tuple[_HeadingCandidate, ...]:
    """
    Identify SEC Item headings from raw HTML structure.

    Candidate boundaries are taken from the raw element ranges, never from
    parser-generated character offsets.
    """
    del base_form

    tokens = _scan_raw_html(content)
    elements = _build_raw_html_elements(content, tokens)
    parents = _build_html_parent_indexes(elements)

    candidates: list[_HeadingCandidate] = []

    semantic_heading_tags = {
        b"h1",
        b"h2",
        b"h3",
        b"h4",
        b"h5",
        b"h6",
    }

    for element_index, element in enumerate(elements):
        if not _is_heading_like_element(element.tag_name):
            continue

        if _has_navigation_ancestor(elements, parents, element_index):
            continue

        text = _normalize_html_heading_text(
            _element_text(content, tokens, element)
        )

        if not text or len(text) > 500:
            continue

        match = _ITEM_HEADING_PATTERN.match(text)

        if not match:
            continue

        if _has_terminal_page_number(text):
            continue

        candidate_index = element_index

        if element.tag_name not in semantic_heading_tags:
            if _has_descendant_complete_item_heading(
                elements,
                tokens,
                content,
                element,
            ):
                continue

            complete_heading = _find_complete_heading_container(
                elements,
                parents,
                tokens,
                content,
                element_index,
            )

            if complete_heading is not None:
                candidate_index, text = complete_heading
                element = elements[candidate_index]

                if _has_navigation_ancestor(
                    elements,
                    parents,
                    candidate_index,
                ):
                    continue

                if _has_terminal_page_number(text):
                    continue

                match = _ITEM_HEADING_PATTERN.match(text)

                if match is None:
                    continue

        number = match.group("number")
        letter = match.group("letter") or ""
        item_number = f"{number}{letter}".upper()
        title = match.group("title").strip()

        if not title:
            continue

        candidates.append(
            _HeadingCandidate(
                item_number=item_number,
                title=title,
                start_offset=element.start_offset,
                end_offset=element.end_offset,
            )
        )

    return _deduplicate_nested_heading_candidates(candidates)


# ---------------------------------------------------------------------------
# Filing section taxonomy
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Heading recognition
# ---------------------------------------------------------------------------
