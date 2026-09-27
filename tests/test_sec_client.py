from __future__ import annotations
from typing import Any
from unittest.mock import Mock
import pytest
import requests
from client.sec_client import SECClient, SECConfigurationError, SECDataError, SECRequestError, SECTickerNotFoundError

class DummyResponse:

    def __init__(self, status_code: int=200, json_data: Any=None, content: bytes=b'', headers: dict[str, str] | None=None) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.content = content
        self.headers = headers or {}

    def json(self) -> Any:
        if isinstance(self._json_data, BaseException):
            raise self._json_data
        return self._json_data

class DummySession:

    def __init__(self, response: Any) -> None:
        self.response = response
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, float]] = []
        self.closed = False

    def get(self, url: str, timeout: float=10.0) -> Any:
        self.calls.append((url, timeout))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    def close(self) -> None:
        self.closed = True

@pytest.fixture
def session() -> DummySession:
    return DummySession(DummyResponse(status_code=200, json_data={'ok': True}))

@pytest.fixture
def client(session: DummySession) -> SECClient:
    return SECClient(name='Test Application', email='test@example.com', organization='Test Organization', requests_per_second=8, timeout=10, session=session)

def test_client_builds_sec_user_agent(session: DummySession) -> None:
    client = SECClient(name='Test Application', email='test@example.com', organization='Test Organization', session=session)
    assert client.user_agent == 'Test Application (test@example.com); Test Organization'
    assert session.headers['User-Agent'] == client.user_agent

def test_client_builds_user_agent_without_organization(session: DummySession) -> None:
    client = SECClient(name='Test Application', email='test@example.com', session=session)
    assert client.user_agent == 'Test Application (test@example.com)'

@pytest.mark.parametrize('kwargs', [{'name': '', 'email': 'test@example.com'}, {'name': '   ', 'email': 'test@example.com'}, {'name': 'Test', 'email': ''}, {'name': 'Test', 'email': '   '}])
def test_invalid_identity_is_rejected(session: DummySession, kwargs: dict[str, str]) -> None:
    with pytest.raises(SECConfigurationError):
        SECClient(session=session, **kwargs)

@pytest.mark.parametrize('requests_per_second', [0, -1, 10.1])
def test_invalid_rate_limit_is_rejected(session: DummySession, requests_per_second: float) -> None:
    with pytest.raises(SECConfigurationError):
        SECClient(name='Test', email='test@example.com', requests_per_second=requests_per_second, session=session)

def test_non_numeric_rate_limit_is_rejected(session: DummySession) -> None:
    with pytest.raises(SECConfigurationError):
        SECClient(name='Test', email='test@example.com', requests_per_second='8', session=session)

@pytest.mark.parametrize('timeout', [0, -1])
def test_invalid_timeout_is_rejected(session: DummySession, timeout: float) -> None:
    with pytest.raises(SECConfigurationError):
        SECClient(name='Test', email='test@example.com', timeout=timeout, session=session)

def test_get_json_returns_dictionary(client: SECClient, session: DummySession) -> None:
    payload = {'facts': {'us-gaap': {}}}
    session.response = DummyResponse(status_code=200, json_data=payload)
    result = client._get_json('https://example.com/data.json')
    assert result == payload
    assert session.calls == [('https://example.com/data.json', 10.0)]

def test_get_json_rejects_non_200_response(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=403)
    with pytest.raises(SECRequestError):
        client._get_json('https://example.com/data.json')

def test_get_json_wraps_request_exception(client: SECClient, session: DummySession) -> None:
    session.response = requests.RequestException('connection failed')
    with pytest.raises(SECRequestError):
        client._get_json('https://example.com/data.json')

def test_get_json_rejects_malformed_json(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=200, json_data=ValueError('invalid JSON'))
    with pytest.raises(SECDataError):
        client._get_json('https://example.com/data.json')

@pytest.mark.parametrize('payload', [[], 'text', 123, None])
def test_get_json_rejects_non_dictionary_payload(client: SECClient, session: DummySession, payload: Any) -> None:
    session.response = DummyResponse(status_code=200, json_data=payload)
    with pytest.raises(SECDataError):
        client._get_json('https://example.com/data.json')

def test_get_content_returns_bytes_and_content_type(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=200, content=b'<html>SEC filing</html>', headers={'Content-Type': 'text/html'})
    content, content_type = client._get_content('https://example.com/filing.htm')
    assert content == b'<html>SEC filing</html>'
    assert content_type == 'text/html'

def test_get_content_rejects_non_200_response(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=404)
    with pytest.raises(SECRequestError):
        client._get_content('https://example.com/filing.htm')

def test_get_content_rejects_empty_content(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=200, content=b'')
    with pytest.raises(SECDataError):
        client._get_content('https://example.com/filing.htm')

def test_get_content_wraps_request_exception(client: SECClient, session: DummySession) -> None:
    session.response = requests.RequestException('connection failed')
    with pytest.raises(SECRequestError):
        client._get_content('https://example.com/filing.htm')

def test_load_ticker_map_normalizes_valid_entries(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=200, json_data={'0': {'ticker': 'msft', 'cik_str': 789019}, '1': {'ticker': ' AAPL ', 'cik_str': '320193'}})
    result = client._load_ticker_map()
    assert result == {'MSFT': 789019, 'AAPL': 320193}

def test_load_ticker_map_ignores_invalid_entries(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=200, json_data={'0': {'ticker': 'MSFT', 'cik_str': 789019}, '1': {'ticker': '', 'cik_str': 123}, '2': {'ticker': 123, 'cik_str': 456}, '3': {'ticker': 'BAD', 'cik_str': 'not-a-number'}, '4': 'invalid'})
    result = client._load_ticker_map()
    assert result == {'MSFT': 789019}

def test_load_ticker_map_rejects_empty_usable_mapping(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=200, json_data={'0': {'ticker': '', 'cik_str': 'invalid'}})
    with pytest.raises(SECDataError):
        client._load_ticker_map()

def test_load_ticker_map_is_cached(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=200, json_data={'0': {'ticker': 'MSFT', 'cik_str': 789019}})
    first = client._load_ticker_map()
    second = client._load_ticker_map()
    assert first is second
    assert len(session.calls) == 1

def test_get_cik_by_ticker_normalizes_ticker(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=200, json_data={'0': {'ticker': 'MSFT', 'cik_str': 789019}})
    assert client.get_cik_by_ticker(' msft ') == 789019

@pytest.mark.parametrize('ticker', ['', '   ', None, 123])
def test_get_cik_by_ticker_rejects_invalid_ticker(client: SECClient, ticker: Any) -> None:
    with pytest.raises(SECConfigurationError):
        client.get_cik_by_ticker(ticker)

def test_get_cik_by_ticker_raises_when_not_found(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=200, json_data={'0': {'ticker': 'AAPL', 'cik_str': 320193}})
    with pytest.raises(SECTickerNotFoundError):
        client.get_cik_by_ticker('MSFT')

def test_get_company_facts_by_cik_zero_pads_cik(client: SECClient, session: DummySession) -> None:
    payload = {'entityName': 'Microsoft Corporation', 'facts': {'us-gaap': {}}}
    session.response = DummyResponse(status_code=200, json_data=payload)
    result = client.get_company_facts_by_cik(789019)
    assert result == payload
    assert session.calls == [('https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json', 10.0)]

@pytest.mark.parametrize('cik', ['789019', True, False, 1.5])
def test_get_company_facts_by_cik_rejects_invalid_type(client: SECClient, cik: Any) -> None:
    with pytest.raises(SECConfigurationError):
        client.get_company_facts_by_cik(cik)

def test_get_company_facts_by_cik_rejects_negative_cik(client: SECClient) -> None:
    with pytest.raises(SECConfigurationError):
        client.get_company_facts_by_cik(-1)

def test_get_company_facts_by_cik_rejects_missing_facts(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=200, json_data={'entityName': 'Microsoft Corporation'})
    with pytest.raises(SECDataError):
        client.get_company_facts_by_cik(789019)

@pytest.mark.parametrize('facts', [None, [], 'invalid', 123])
def test_get_company_facts_by_cik_rejects_invalid_facts(client: SECClient, session: DummySession, facts: Any) -> None:
    session.response = DummyResponse(status_code=200, json_data={'facts': facts})
    with pytest.raises(SECDataError):
        client.get_company_facts_by_cik(789019)

def test_get_company_facts_by_ticker(client: SECClient, session: DummySession) -> None:
    session.response = DummyResponse(status_code=200, json_data={'0': {'ticker': 'MSFT', 'cik_str': 789019}})
    session.response = DummyResponse(status_code=200, json_data={'facts': {'us-gaap': {}}})
    client._get_json = Mock(side_effect=[{'0': {'ticker': 'MSFT', 'cik_str': 789019}}, {'facts': {'us-gaap': {}}}])
    result = client.get_company_facts_by_ticker('MSFT')
    assert result == {'facts': {'us-gaap': {}}}
    assert client._get_json.call_count == 2

def test_rate_limit_does_not_sleep_when_interval_has_elapsed(client: SECClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monotonic_values = iter([10.0, 10.0])
    monkeypatch.setattr('client.sec_client.time.monotonic', lambda: next(monotonic_values))
    sleep_mock = Mock()
    monkeypatch.setattr('client.sec_client.time.sleep', sleep_mock)
    client._last_request_time = 0.0
    client._rate_limit()
    sleep_mock.assert_not_called()

def test_rate_limit_sleeps_when_requests_are_too_close(client: SECClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monotonic_values = iter([0.0, 0.01])
    monkeypatch.setattr('client.sec_client.time.monotonic', lambda: next(monotonic_values))
    sleep_mock = Mock()
    monkeypatch.setattr('client.sec_client.time.sleep', sleep_mock)
    client._last_request_time = 0.0
    client._rate_limit()
    sleep_mock.assert_called_once()

def test_close_closes_underlying_session(client: SECClient, session: DummySession) -> None:
    client.close()
    assert session.closed is True

def test_context_manager_closes_session(session: DummySession) -> None:
    with SECClient(name='Test Application', email='test@example.com', session=session):
        pass
    assert session.closed is True