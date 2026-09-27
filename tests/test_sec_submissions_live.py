from client.sec_client import SECClient
from client.sec_submissions import SECFiling, SubmissionsClient
SEC_NAME = 'Schwab Market Data Pilot'
SEC_EMAIL = 'YOUR_EMAIL@example.com'
SEC_ORGANIZATION = 'Schwab Market Data Pilot'

def test_msft_live_submissions() -> None:
    client = SECClient(name=SEC_NAME, email=SEC_EMAIL, organization=SEC_ORGANIZATION)
    submissions_client = SubmissionsClient(client)
    filings = submissions_client.get_recent_filings_by_ticker('MSFT')
    assert filings
    assert all((isinstance(filing, SECFiling) for filing in filings))
    assert all((filing.cik == 789019 for filing in filings))
    assert all((filing.form for filing in filings))
    assert all((filing.accession_number for filing in filings))
    assert all((filing.filing_date for filing in filings))
    assert all((filing.report_date is None or isinstance(filing.report_date, str) for filing in filings))
    assert all((len(filing.accession_number.split('-')) == 3 for filing in filings))