"""
sec_ownership.py

Deterministic SEC Section 16 ownership parsing.

Responsibilities:
- Parse SEC Forms 3, 3/A, 4, 4/A, 5, and 5/A ownership XML.
- Preserve SEC-reported ownership and transaction facts without interpretation.
- Preserve exact source bytes through SECFilingDocument.
- Produce immutable, provenance-bearing ownership records.

This module does NOT:
- perform HTTP transport
- perform SEC rate limiting
- discover filings
- retrieve filing documents
- interpret ownership activity
- calculate investment metrics
- reconcile original/amended filings
- perform LLM reasoning

Architecture:

    SECClient
        |
        v
    SubmissionsClient
        |
        v
    SECFilingsClient
        |
        v
    SECFilingDocument
        |
        v
    sec_ownership.py
        |
        +--> deterministic XML parsing
        +--> immutable ownership records
        +--> provenance
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    from sec_filings import SECFilingDocument


SUPPORTED_OWNERSHIP_FORMS = frozenset(
    {
        "3",
        "3/A",
        "4",
        "4/A",
        "5",
        "5/A",
    }
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPACT_DATE_RE = re.compile(r"^\d{8}$")
_CIK_RE = re.compile(r"^\d{1,10}$")


class SECOwnershipError(Exception):
    """Base exception for SEC ownership processing."""


class SECOwnershipFormatError(SECOwnershipError):
    """Raised when ownership XML is malformed or structurally invalid."""


class SECOwnershipValidationError(SECOwnershipError):
    """Raised when ownership input violates a required contract."""


@dataclass(frozen=True)
class SECReportingRelationship:
    """SEC-reported relationship between a reporting person and issuer."""

    is_director: bool
    is_officer: bool
    officer_title: str | None
    is_ten_percent_owner: bool
    is_other: bool
    other_description: str | None


@dataclass(frozen=True)
class SECReportingPerson:
    """SEC-reported Section 16 reporting person."""

    name: str
    cik: str | None
    relationship: SECReportingRelationship
    address: str | None


@dataclass(frozen=True)
class SECOwnershipProvenance:
    """Immutable identity of the authoritative SEC source."""

    cik: str
    accession_number: str
    form: str
    filing_date: date
    source_url: str
    content_sha256: str


@dataclass(frozen=True)
class SECNonDerivativeOwnership:
    """SEC-reported non-derivative transaction or holding."""

    security_title: str
    transaction_date: date | None
    deemed_execution_date: date | None
    transaction_code: str | None
    acquired_disposed: str | None
    shares: Decimal | None
    price: Decimal | None
    shares_owned_following: Decimal | None
    ownership_form: str | None
    indirect_ownership_nature: str | None


@dataclass(frozen=True)
class SECDerivativeOwnership:
    """SEC-reported derivative transaction or holding."""

    security_title: str
    transaction_date: date | None
    deemed_execution_date: date | None
    transaction_code: str | None
    acquired_disposed: str | None
    shares: Decimal | None
    price: Decimal | None
    shares_owned_following: Decimal | None
    date_exercisable: date | None
    expiration_date: date | None
    underlying_security_title: str | None
    underlying_shares: Decimal | None
    ownership_form: str | None
    indirect_ownership_nature: str | None


@dataclass(frozen=True)
class SECOwnershipFiling:
    """Immutable parsed representation of one SEC ownership filing."""

    cik: str
    accession_number: str
    form: str
    filing_date: date
    report_date: date | None
    issuer_name: str
    issuer_cik: str | None
    issuer_ticker: str | None
    reporting_persons: tuple[SECReportingPerson, ...]
    non_derivative_ownership: tuple[SECNonDerivativeOwnership, ...]
    derivative_ownership: tuple[SECDerivativeOwnership, ...]
    provenance: SECOwnershipProvenance
    source_document: SECFilingDocument


def parse_ownership_filing(
    document: SECFilingDocument,
) -> SECOwnershipFiling:
    """
    Parse one SEC Section 16 ownership XML document.

    The exact bytes in SECFilingDocument remain authoritative.
    XML parsing is used only to interpret the existing source bytes.
    """
    if document is None:
        raise SECOwnershipValidationError(
            "document must not be None."
        )

    filing = document.filing

    if not isinstance(filing.cik, int) or isinstance(
        filing.cik,
        bool,
    ):
        raise SECOwnershipValidationError(
            "filing CIK must be an integer."
        )

    if filing.cik < 0:
        raise SECOwnershipValidationError(
            "filing CIK must be non-negative."
        )

    form = _normalize_form(filing.form)

    if form not in SUPPORTED_OWNERSHIP_FORMS:
        raise SECOwnershipValidationError(
            f"unsupported ownership form: {filing.form!r}"
        )

    accession_number = _required_string(
        filing.accession_number,
        "accession number",
    )

    filing_date = _parse_required_date(
        filing.filing_date,
        "filing date",
    )

    content = document.content

    if not isinstance(content, bytes):
        raise SECOwnershipValidationError(
            "SECFilingDocument.content must be bytes."
        )

    if not content:
        raise SECOwnershipFormatError(
            "ownership filing document contains no content."
        )

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise SECOwnershipFormatError(
            "ownership filing is not valid XML."
        ) from exc

    if _local_name(root.tag) != "ownershipDocument":
        raise SECOwnershipFormatError(
            "ownership XML root must be ownershipDocument."
        )

    issuer = _first_child(root, "issuer")

    issuer_name = _text_from_child(
        issuer,
        "issuerName",
    )

    if issuer_name is None:
        issuer_name = ""

    issuer_cik = _normalize_optional_cik(
        _text_from_child(
            issuer,
            "issuerCik",
        )
    )

    issuer_ticker = _text_from_child(
        issuer,
        "issuerTradingSymbol",
    )

    reporting_persons = tuple(
        _parse_reporting_person(node)
        for node in _children(
            root,
            "reportingOwner",
        )
    )

    non_derivative_records = tuple(
        _parse_non_derivative(node)
        for node in _descendants(
            root,
            "nonDerivativeTransaction",
        )
    )

    derivative_records = tuple(
        _parse_derivative(node)
        for node in _descendants(
            root,
            "derivativeTransaction",
        )
    )

    report_date = _parse_optional_date(
        _text_from_child(
            root,
            "periodOfReport",
        )
    )

    # SECFilingDocument.content_hash is authoritative because it was
    # calculated by the retrieval layer from the exact SEC response bytes.
    content_sha256 = document.content_hash

    if not isinstance(content_sha256, str) or not content_sha256:
        raise SECOwnershipValidationError(
            "SECFilingDocument.content_hash must be a non-empty string."
        )

    source_url = document.source_url

    if not isinstance(source_url, str) or not source_url:
        raise SECOwnershipValidationError(
            "SECFilingDocument.source_url must be a non-empty string."
        )

    normalized_cik = str(filing.cik).zfill(10)

    provenance = SECOwnershipProvenance(
        cik=normalized_cik,
        accession_number=accession_number,
        form=form,
        filing_date=filing_date,
        source_url=source_url,
        content_sha256=content_sha256,
    )

    return SECOwnershipFiling(
        cik=normalized_cik,
        accession_number=accession_number,
        form=form,
        filing_date=filing_date,
        report_date=report_date,
        issuer_name=issuer_name,
        issuer_cik=issuer_cik,
        issuer_ticker=issuer_ticker,
        reporting_persons=reporting_persons,
        non_derivative_ownership=non_derivative_records,
        derivative_ownership=derivative_records,
        provenance=provenance,
        source_document=document,
    )


def _parse_reporting_person(
    node: ET.Element,
) -> SECReportingPerson:
    """Parse one reportingOwner element."""

    identification = _first_child(
        node,
        "reportingOwnerId",
    )

    relationship = _first_child(
        node,
        "reportingOwnerRelationship",
    )

    address = _first_child(
        node,
        "reportingOwnerAddress",
    )

    name = _text_from_child(
        identification,
        "rptOwnerName",
    )

    if name is None:
        raise SECOwnershipFormatError(
            "reporting owner is missing rptOwnerName."
        )

    cik = _normalize_optional_cik(
        _text_from_child(
            identification,
            "rptOwnerCik",
        )
    )

    is_director = _parse_bool(
        _text_from_child(
            relationship,
            "isDirector",
        )
    )

    is_officer = _parse_bool(
        _text_from_child(
            relationship,
            "isOfficer",
        )
    )

    officer_title = _text_from_child(
        relationship,
        "officerTitle",
    )

    is_ten_percent_owner = _parse_bool(
        _text_from_child(
            relationship,
            "isTenPercentOwner",
        )
    )

    is_other = _parse_bool(
        _text_from_child(
            relationship,
            "isOther",
        )
    )

    other_description = _text_from_child(
        relationship,
        "otherText",
    )

    return SECReportingPerson(
        name=name,
        cik=cik,
        relationship=SECReportingRelationship(
            is_director=is_director,
            is_officer=is_officer,
            officer_title=officer_title,
            is_ten_percent_owner=is_ten_percent_owner,
            is_other=is_other,
            other_description=other_description,
        ),
        address=_parse_address(address),
    )


def _parse_non_derivative(
    node: ET.Element,
) -> SECNonDerivativeOwnership:
    """Parse one nonDerivativeTransaction element."""

    security_title = _text_from_path(
        node,
        (
            "securityTitle",
            "value",
        ),
    )

    if security_title is None:
        security_title = ""

    return SECNonDerivativeOwnership(
        security_title=security_title,
        transaction_date=_parse_optional_date(
            _text_from_path(
                node,
                (
                    "transactionDate",
                    "value",
                ),
            )
        ),
        deemed_execution_date=_parse_optional_date(
            _text_from_path(
                node,
                (
                    "deemedExecutionDate",
                    "value",
                ),
            )
        ),
        transaction_code=_text_from_path(
            node,
            (
                "transactionCoding",
                "transactionCode",
            ),
        ),
        acquired_disposed=_text_from_path(
            node,
            (
                "transactionAmounts",
                "transactionAcquiredDisposedCode",
                "value",
            ),
        ),
        shares=_parse_decimal(
            _text_from_path(
                node,
                (
                    "transactionAmounts",
                    "transactionShares",
                    "value",
                ),
            ),
            "transaction shares",
        ),
        price=_parse_decimal(
            _text_from_path(
                node,
                (
                    "transactionAmounts",
                    "transactionPricePerShare",
                    "value",
                ),
            ),
            "transaction price",
        ),
        shares_owned_following=_parse_decimal(
            _text_from_path(
                node,
                (
                    "postTransactionAmounts",
                    "sharesOwnedFollowingTransaction",
                    "value",
                ),
            ),
            "shares owned following transaction",
        ),
        ownership_form=_text_from_path(
            node,
            (
                "ownershipNature",
                "directOrIndirectOwnership",
                "value",
            ),
        ),
        indirect_ownership_nature=_text_from_path(
            node,
            (
                "ownershipNature",
                "natureOfOwnership",
                "value",
            ),
        ),
    )


def _parse_derivative(
    node: ET.Element,
) -> SECDerivativeOwnership:
    """Parse one derivativeTransaction element."""

    security_title = _text_from_path(
        node,
        (
            "securityTitle",
            "value",
        ),
    )

    if security_title is None:
        security_title = ""

    return SECDerivativeOwnership(
        security_title=security_title,
        transaction_date=_parse_optional_date(
            _text_from_path(
                node,
                (
                    "transactionDate",
                    "value",
                ),
            )
        ),
        deemed_execution_date=_parse_optional_date(
            _text_from_path(
                node,
                (
                    "deemedExecutionDate",
                    "value",
                ),
            )
        ),
        transaction_code=_text_from_path(
            node,
            (
                "transactionCoding",
                "transactionCode",
            ),
        ),
        acquired_disposed=_text_from_path(
            node,
            (
                "transactionAmounts",
                "transactionAcquiredDisposedCode",
                "value",
            ),
        ),
        shares=_parse_decimal(
            _text_from_path(
                node,
                (
                    "transactionAmounts",
                    "transactionShares",
                    "value",
                ),
            ),
            "derivative transaction shares",
        ),
        price=_parse_decimal(
            _text_from_path(
                node,
                (
                    "transactionAmounts",
                    "transactionPricePerShare",
                    "value",
                ),
            ),
            "derivative transaction price",
        ),
        shares_owned_following=_parse_decimal(
            _text_from_path(
                node,
                (
                    "postTransactionAmounts",
                    "sharesOwnedFollowingTransaction",
                    "value",
                ),
            ),
            "derivative shares owned following transaction",
        ),
        date_exercisable=_parse_optional_date(
            _text_from_path(
                node,
                (
                    "exerciseDate",
                    "value",
                ),
            )
        ),
        expiration_date=_parse_optional_date(
            _text_from_path(
                node,
                (
                    "expirationDate",
                    "value",
                ),
            )
        ),
        underlying_security_title=_text_from_path(
            node,
            (
                "underlyingSecurity",
                "underlyingSecurityTitle",
                "value",
            ),
        ),
        underlying_shares=_parse_decimal(
            _text_from_path(
                node,
                (
                    "underlyingSecurity",
                    "underlyingSecurityShares",
                    "value",
                ),
            ),
            "underlying shares",
        ),
        ownership_form=_text_from_path(
            node,
            (
                "ownershipNature",
                "directOrIndirectOwnership",
                "value",
            ),
        ),
        indirect_ownership_nature=_text_from_path(
            node,
            (
                "ownershipNature",
                "natureOfOwnership",
                "value",
            ),
        ),
    )


def _parse_address(
    node: ET.Element | None,
) -> str | None:
    """Return the SEC-reported address as normalized text."""

    if node is None:
        return None

    parts: list[str] = []

    for child in node:
        text = _element_text(child)

        if text:
            parts.append(text)

    if parts:
        return ", ".join(parts)

    return _element_text(node)


def _normalize_form(
    form: object,
) -> str:
    """Normalize a filing form."""

    if not isinstance(form, str):
        raise SECOwnershipValidationError(
            "filing form must be a string."
        )

    normalized = form.strip().upper()

    if not normalized:
        raise SECOwnershipValidationError(
            "filing form must not be empty."
        )

    return normalized


def _required_string(
    value: object,
    field_name: str,
) -> str:
    """Validate a required non-empty string."""

    if not isinstance(value, str) or not value.strip():
        raise SECOwnershipValidationError(
            f"{field_name} must be a non-empty string."
        )

    return value.strip()


def _normalize_optional_cik(
    value: str | None,
) -> str | None:
    """Normalize an optional SEC CIK."""

    if value is None:
        return None

    value = value.strip()

    if not value:
        return None

    if not _CIK_RE.fullmatch(value):
        raise SECOwnershipFormatError(
            f"invalid SEC CIK: {value!r}"
        )

    return value.zfill(10)


def _parse_required_date(
    value: object,
    field_name: str,
) -> date:
    """Parse a required SEC date."""

    if not isinstance(value, str):
        raise SECOwnershipValidationError(
            f"{field_name} must be a string."
        )

    parsed = _parse_optional_date(value)

    if parsed is None:
        raise SECOwnershipValidationError(
            f"{field_name} must not be empty."
        )

    return parsed


def _parse_optional_date(
    value: str | None,
) -> date | None:
    """Parse supported SEC date representations."""

    if value is None:
        return None

    value = value.strip()

    if not value:
        return None

    try:
        if _DATE_RE.fullmatch(value):
            return date.fromisoformat(value)

        if _COMPACT_DATE_RE.fullmatch(value):
            return date(
                int(value[0:4]),
                int(value[4:6]),
                int(value[6:8]),
            )
    except ValueError as exc:
        raise SECOwnershipFormatError(
            f"invalid SEC date: {value!r}"
        ) from exc

    raise SECOwnershipFormatError(
        f"unsupported SEC date format: {value!r}"
    )


def _parse_decimal(
    value: str | None,
    field_name: str,
) -> Decimal | None:
    """
    Parse an SEC numeric value exactly.

    Missing values remain None.
    Explicit zero remains Decimal("0").
    """

    if value is None:
        return None

    value = value.strip()

    if not value:
        return None

    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise SECOwnershipFormatError(
            f"invalid decimal for {field_name}: {value!r}"
        ) from exc


def _parse_bool(
    value: str | None,
) -> bool:
    """Parse SEC boolean values deterministically."""

    if value is None:
        return False

    normalized = value.strip().lower()

    if normalized in {
        "1",
        "true",
        "yes",
    }:
        return True

    if normalized in {
        "0",
        "false",
        "no",
    }:
        return False

    raise SECOwnershipFormatError(
        f"invalid SEC boolean value: {value!r}"
    )


def _local_name(
    tag: str,
) -> str:
    """Return an XML tag's namespace-independent local name."""

    if "}" in tag:
        return tag.rsplit(
            "}",
            1,
        )[1]

    return tag


def _children(
    node: ET.Element | None,
    name: str,
) -> tuple[ET.Element, ...]:
    """Return matching direct children."""

    if node is None:
        return ()

    return tuple(
        child
        for child in node
        if _local_name(child.tag) == name
    )


def _first_child(
    node: ET.Element | None,
    name: str,
) -> ET.Element | None:
    """Return the first matching direct child."""

    if node is None:
        return None

    for child in node:
        if _local_name(child.tag) == name:
            return child

    return None


def _descendants(
    node: ET.Element,
    name: str,
) -> tuple[ET.Element, ...]:
    """Return matching descendants."""

    return tuple(
        child
        for child in node.iter()
        if child is not node
        and _local_name(child.tag) == name
    )


def _text_from_child(
    node: ET.Element | None,
    name: str,
) -> str | None:
    """Return normalized text from a direct child."""

    child = _first_child(
        node,
        name,
    )

    return _element_text(child)


def _text_from_path(
    node: ET.Element | None,
    path: tuple[str, ...],
) -> str | None:
    """Return normalized text from a deterministic XML path."""

    current = node

    for name in path:
        current = _first_child(
            current,
            name,
        )

        if current is None:
            return None

    return _element_text(current)


def _element_text(
    node: ET.Element | None,
) -> str | None:
    """Return normalized textual content from an XML element."""

    if node is None:
        return None

    text = "".join(
        node.itertext()
    )

    normalized = " ".join(
        text.split()
    )

    return normalized or None


__all__ = [
    "SUPPORTED_OWNERSHIP_FORMS",
    "SECOwnershipError",
    "SECOwnershipFormatError",
    "SECOwnershipValidationError",
    "SECReportingRelationship",
    "SECReportingPerson",
    "SECOwnershipProvenance",
    "SECNonDerivativeOwnership",
    "SECDerivativeOwnership",
    "SECOwnershipFiling",
    "parse_ownership_filing",
]