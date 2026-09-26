from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256

import pytest

from sec_filings import SECFilingDocument
from sec_ownership import (
    SECNonDerivativeOwnership,
    SECReportingPerson,
    SECDerivativeOwnership,
    SECOwnershipError,
    SECOwnershipFormatError,
    SECOwnershipValidationError,
    parse_ownership_filing,
)
from sec_submissions import SECFiling


ACCESSION = "0001234567-26-000001"
FILING_DATE = "2026-09-15"
REPORT_DATE = "2026-09-14"
SOURCE_URL = (
    "https://www.sec.gov/Archives/edgar/data/"
    "1234567/000123456726000001/form4.xml"
)


def _ownership_xml(
    *,
    include_derivative: bool = True,
    namespace: bool = False,
) -> bytes:
    prefix = "ns:" if namespace else ""
    ns_decl = ' xmlns:ns="urn:sec:test"' if namespace else ""

    derivative = ""
    if include_derivative:
        derivative = f"""
  <{prefix}derivativeTable>
    <{prefix}derivativeTransaction>
      <{prefix}securityTitle>
        <{prefix}value>Employee Stock Option</{prefix}value>
      </{prefix}securityTitle>

      <{prefix}transactionDate>
        <{prefix}value>{FILING_DATE}</{prefix}value>
      </{prefix}transactionDate>

      <{prefix}transactionCoding>
        <{prefix}transactionCode>A</{prefix}transactionCode>
      </{prefix}transactionCoding>

      <{prefix}transactionAmounts>
        <{prefix}transactionShares>
          <{prefix}value>0</{prefix}value>
        </{prefix}transactionShares>
        <{prefix}transactionPricePerShare>
          <{prefix}value>0</{prefix}value>
        </{prefix}transactionPricePerShare>
        <{prefix}transactionAcquiredDisposedCode>
          <{prefix}value>A</{prefix}value>
        </{prefix}transactionAcquiredDisposedCode>
      </{prefix}transactionAmounts>

      <{prefix}postTransactionAmounts>
        <{prefix}sharesOwnedFollowingTransaction>
          <{prefix}value>2500</{prefix}value>
        </{prefix}sharesOwnedFollowingTransaction>
      </{prefix}postTransactionAmounts>

      <{prefix}exerciseDate>
        <{prefix}value>2027-09-15</{prefix}value>
      </{prefix}exerciseDate>

      <{prefix}expirationDate>
        <{prefix}value>2030-09-15</{prefix}value>
      </{prefix}expirationDate>

      <{prefix}underlyingSecurity>
        <{prefix}underlyingSecurityTitle>
          <{prefix}value>Common Stock</{prefix}value>
        </{prefix}underlyingSecurityTitle>
        <{prefix}underlyingSecurityShares>
          <{prefix}value>2500</{prefix}value>
        </{prefix}underlyingSecurityShares>
      </{prefix}underlyingSecurity>

      <{prefix}ownershipNature>
        <{prefix}directOrIndirectOwnership>
          <{prefix}value>D</{prefix}value>
        </{prefix}directOrIndirectOwnership>
        <{prefix}natureOfOwnership>
          <{prefix}value>Direct</{prefix}value>
        </{prefix}natureOfOwnership>
      </{prefix}ownershipNature>
    </{prefix}derivativeTransaction>
  </{prefix}derivativeTable>
"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<{prefix}ownershipDocument{ns_decl}>
  <{prefix}schemaVersion>5.0</{prefix}schemaVersion>
  <{prefix}documentType>4</{prefix}documentType>

  <{prefix}periodOfReport>{REPORT_DATE}</{prefix}periodOfReport>

  <{prefix}issuer>
    <{prefix}issuerCik>0000789019</{prefix}issuerCik>
    <{prefix}issuerName>MICROSOFT CORPORATION</{prefix}issuerName>
    <{prefix}issuerTradingSymbol>MSFT</{prefix}issuerTradingSymbol>
  </{prefix}issuer>

  <{prefix}reportingOwner>
    <{prefix}reportingOwnerId>
      <{prefix}rptOwnerCik>0001234567</{prefix}rptOwnerCik>
      <{prefix}rptOwnerName>Jane Doe</{prefix}rptOwnerName>
    </{prefix}reportingOwnerId>

    <{prefix}reportingOwnerAddress>
      <{prefix}rptOwnerStreet1>1 Main Street</{prefix}rptOwnerStreet1>
      <{prefix}rptOwnerStreet2>Suite 100</{prefix}rptOwnerStreet2>
      <{prefix}rptOwnerCity>Redmond</{prefix}rptOwnerCity>
      <{prefix}rptOwnerState>WA</{prefix}rptOwnerState>
      <{prefix}rptOwnerZipCode>98052</{prefix}rptOwnerZipCode>
      <{prefix}rptOwnerStateDescription>Washington</{prefix}rptOwnerStateDescription>
    </{prefix}reportingOwnerAddress>

    <{prefix}reportingOwnerRelationship>
      <{prefix}isDirector>0</{prefix}isDirector>
      <{prefix}isOfficer>1</{prefix}isOfficer>
      <{prefix}officerTitle>Chief Financial Officer</{prefix}officerTitle>
      <{prefix}isTenPercentOwner>0</{prefix}isTenPercentOwner>
      <{prefix}isOther>0</{prefix}isOther>
    </{prefix}reportingOwnerRelationship>
  </{prefix}reportingOwner>

  <{prefix}nonDerivativeTable>
    <{prefix}nonDerivativeTransaction>
      <{prefix}securityTitle>
        <{prefix}value>Common Stock</{prefix}value>
      </{prefix}securityTitle>

      <{prefix}transactionDate>
        <{prefix}value>{FILING_DATE}</{prefix}value>
      </{prefix}transactionDate>

      <{prefix}transactionCoding>
        <{prefix}transactionFormType>4</{prefix}transactionFormType>
        <{prefix}transactionCode>S</{prefix}transactionCode>
        <{prefix}equitySwapInvolved>0</{prefix}equitySwapInvolved>
      </{prefix}transactionCoding>

      <{prefix}transactionAmounts>
        <{prefix}transactionShares>
          <{prefix}value>100.50</{prefix}value>
        </{prefix}transactionShares>
        <{prefix}transactionPricePerShare>
          <{prefix}value>425.25</{prefix}value>
        </{prefix}transactionPricePerShare>
        <{prefix}transactionAcquiredDisposedCode>
          <{prefix}value>D</{prefix}value>
        </{prefix}transactionAcquiredDisposedCode>
      </{prefix}transactionAmounts>

      <{prefix}postTransactionAmounts>
        <{prefix}sharesOwnedFollowingTransaction>
          <{prefix}value>1000</{prefix}value>
        </{prefix}sharesOwnedFollowingTransaction>
      </{prefix}postTransactionAmounts>

      <{prefix}ownershipNature>
        <{prefix}directOrIndirectOwnership>
          <{prefix}value>D</{prefix}value>
        </{prefix}directOrIndirectOwnership>
        <{prefix}natureOfOwnership>
          <{prefix}value>Direct</{prefix}value>
        </{prefix}natureOfOwnership>
      </{prefix}ownershipNature>
    </{prefix}nonDerivativeTransaction>
  </{prefix}nonDerivativeTable>
{derivative}
</{prefix}ownershipDocument>
""".encode("utf-8")


def _make_document(
    *,
    form: str = "4",
    xml: bytes | None = None,
    filing_date: str = FILING_DATE,
    report_date: str | None = REPORT_DATE,
) -> SECFilingDocument:
    content = xml if xml is not None else _ownership_xml()

    filing = SECFiling(
        cik=1234567,
        form=form,
        accession_number=ACCESSION,
        filing_date=filing_date,
        report_date=report_date,
        primary_document="form4.xml",
        is_amendment=form.endswith("/A"),
        file_number=None,
        filing_url=SOURCE_URL,
    )

    return SECFilingDocument(
        filing=filing,
        document_name="form4.xml",
        document_kind="ownership",
        source_url=SOURCE_URL,
        content_type="application/xml",
        content_hash=sha256(content).hexdigest(),
        content=content,
    )


def test_parse_form4_returns_expected_top_level_metadata() -> None:
    result = parse_ownership_filing(_make_document())

    assert result.cik == "0001234567"
    assert result.accession_number == ACCESSION
    assert result.form == "4"
    assert result.filing_date == date(2026, 9, 15)
    assert result.report_date == date(2026, 9, 14)
    assert result.issuer_name == "MICROSOFT CORPORATION"
    assert result.issuer_cik == "0000789019"
    assert result.issuer_ticker == "MSFT"


def test_provenance_uses_authoritative_document_hash_and_url() -> None:
    document = _make_document()
    result = parse_ownership_filing(document)

    assert result.provenance.cik == "0001234567"
    assert result.provenance.accession_number == ACCESSION
    assert result.provenance.form == "4"
    assert result.provenance.filing_date == date(2026, 9, 15)
    assert result.provenance.source_url == SOURCE_URL
    assert result.provenance.content_sha256 == document.content_hash


def test_reporting_person_metadata_is_parsed() -> None:
    result = parse_ownership_filing(_make_document())

    assert len(result.reporting_persons) == 1

    person = result.reporting_persons[0]

    assert isinstance(person, SECReportingPerson)
    assert person.name == "Jane Doe"
    assert person.cik == "0001234567"
    assert person.relationship.is_director is False
    assert person.relationship.is_officer is True
    assert person.relationship.officer_title == "Chief Financial Officer"
    assert person.relationship.is_ten_percent_owner is False
    assert person.relationship.is_other is False
    assert person.address is not None


def test_non_derivative_transaction_is_parsed_with_decimal_values() -> None:
    result = parse_ownership_filing(_make_document())

    transaction = result.non_derivative_ownership[0]

    assert isinstance(transaction, SECNonDerivativeOwnership)
    assert transaction.security_title == "Common Stock"
    assert transaction.transaction_date == date(2026, 9, 15)
    assert transaction.transaction_code == "S"
    assert transaction.shares == Decimal("100.50")
    assert transaction.price == Decimal("425.25")
    assert transaction.acquired_disposed == "D"
    assert transaction.shares_owned_following == Decimal("1000")
    assert transaction.ownership_form == "D"
    assert transaction.indirect_ownership_nature == "Direct"


def test_derivative_transaction_is_parsed() -> None:
    result = parse_ownership_filing(_make_document())

    transaction = result.derivative_ownership[0]

    assert isinstance(transaction, SECDerivativeOwnership)
    assert transaction.security_title == "Employee Stock Option"
    assert transaction.transaction_date == date(2026, 9, 15)
    assert transaction.transaction_code == "A"
    assert transaction.shares == Decimal("0")
    assert transaction.price == Decimal("0")
    assert transaction.date_exercisable == date(2027, 9, 15)
    assert transaction.expiration_date == date(2030, 9, 15)
    assert transaction.underlying_security_title == "Common Stock"
    assert transaction.underlying_shares == Decimal("2500")


def test_missing_optional_values_are_none() -> None:
    result = parse_ownership_filing(
        _make_document(xml=_ownership_xml(include_derivative=False))
    )

    transaction = result.non_derivative_ownership[0]

    assert transaction.deemed_execution_date is None


def test_explicit_zero_is_preserved_as_decimal_zero() -> None:
    result = parse_ownership_filing(_make_document())

    transaction = result.derivative_ownership[0]

    assert transaction.shares == Decimal("0")
    assert transaction.price == Decimal("0")


def test_namespace_prefixed_ownership_document_is_supported() -> None:
    result = parse_ownership_filing(
        _make_document(xml=_ownership_xml(namespace=True))
    )

    assert result.issuer_name == "MICROSOFT CORPORATION"
    assert len(result.reporting_persons) == 1
    assert len(result.non_derivative_ownership) == 1
    assert len(result.derivative_ownership) == 1


@pytest.mark.parametrize(
    "form",
    ["3", "3/A", "4", "4/A", "5", "5/A"],
)
def test_supported_ownership_forms_are_accepted(form: str) -> None:
    result = parse_ownership_filing(_make_document(form=form))

    assert result.form == form


def test_unsupported_form_is_rejected() -> None:
    with pytest.raises(SECOwnershipValidationError):
        parse_ownership_filing(_make_document(form="10-K"))


def test_invalid_filing_date_is_rejected() -> None:
    with pytest.raises(SECOwnershipFormatError):
        parse_ownership_filing(_make_document(filing_date="not-a-date"))


def test_empty_document_content_is_rejected() -> None:
    with pytest.raises(SECOwnershipFormatError):
        parse_ownership_filing(_make_document(xml=b""))


def test_malformed_xml_is_rejected() -> None:
    with pytest.raises(SECOwnershipFormatError):
        parse_ownership_filing(_make_document(xml=b"<ownershipDocument>"))


def test_wrong_xml_root_is_rejected() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<notAnOwnershipDocument>
    <issuerName>MICROSOFT CORPORATION</issuerName>
</notAnOwnershipDocument>
"""

    with pytest.raises(SECOwnershipFormatError):
        parse_ownership_filing(_make_document(xml=xml))


def test_non_document_input_is_rejected() -> None:
    with pytest.raises(SECOwnershipValidationError):
        parse_ownership_filing(None)  # type: ignore[arg-type]


def test_production_document_remains_immutable() -> None:
    document = _make_document()

    with pytest.raises((AttributeError, TypeError)):
        document.content = b"changed"  # type: ignore[misc]