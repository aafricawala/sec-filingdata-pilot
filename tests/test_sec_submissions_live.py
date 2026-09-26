"""
Live integration tests for the SEC submissions layer.

These tests intentionally call the real SEC EDGAR submissions endpoint.
They validate that the production SECClient and SubmissionsClient work
correctly against current SEC data.
"""

from sec_client import SECClient
from sec_submissions import SECFiling, SubmissionsClient


# Use a real SEC identity for the integration test.
SEC_NAME = "Schwab Market Data Pilot"
SEC_EMAIL = "YOUR_EMAIL@example.com"
SEC_ORGANIZATION = "Schwab Market Data Pilot"


def test_msft_live_submissions() -> None:
    """
    Validate live SEC submissions retrieval for Microsoft.

    This test performs a real SEC API request and verifies only stable
    structural properties rather than exact current filing contents.
    """

    # Create the production SEC client.
    client = SECClient(
        name=SEC_NAME,
        email=SEC_EMAIL,
        organization=SEC_ORGANIZATION,
    )

    # Create the submissions service using the real SEC client.
    submissions_client = SubmissionsClient(client)

    # Retrieve Microsoft's current recent filings from SEC EDGAR.
    filings = submissions_client.get_recent_filings_by_ticker("MSFT")

    # Confirm that SEC returned at least one filing.
    assert filings

    # Confirm every returned record uses our normalized filing model.
    assert all(isinstance(filing, SECFiling) for filing in filings)

    # Confirm the CIK corresponds to Microsoft.
    assert all(filing.cik == 789019 for filing in filings)

    # Confirm core SEC filing metadata is populated.
    assert all(filing.form for filing in filings)
    assert all(filing.accession_number for filing in filings)
    assert all(filing.filing_date for filing in filings)
    
    # Report dates are optional because SEC does not provide
    # reportDate for every filing type.
    assert all(
      filing.report_date is None or isinstance(filing.report_date, str)
      for filing in filings
      )

    # Confirm accession numbers use the expected SEC format.
    assert all(
        len(filing.accession_number.split("-")) == 3
        for filing in filings
    )