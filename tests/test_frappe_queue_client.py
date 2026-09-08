import json
import pytest

from frappe_queue_client import AuthenticationError, FrappeQueueClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f'HTTP {self.status_code}')


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_auth_header_and_retryable_filters():
    session = FakeSession([FakeResponse(payload={'data': [{'name': 'KPQ-1'}]})])
    client = FrappeQueueClient('https://ourcity.s.frappe.cloud/', 'key', 'secret', session=session)
    rows = client.list_retryable(3)
    assert rows == [{'name': 'KPQ-1'}]
    method, url, kwargs = session.calls[0]
    assert method == 'GET'
    assert url == 'https://ourcity.s.frappe.cloud/api/resource/Kitchen%20Print%20Queue'
    assert kwargs['headers']['Authorization'] == 'token key:secret'
    filters = json.loads(kwargs['params']['filters'])
    assert ['Kitchen Print Queue', 'status', 'in', ['Pending', 'Error']] in filters
    assert ['Kitchen Print Queue', 'retry_count', '<', 3] in filters


def test_get_and_update_quote_document_name_and_put_json():
    session = FakeSession([
        FakeResponse(payload={'data': {'name': 'KPQ 1'}}),
        FakeResponse(payload={'data': {'name': 'KPQ 1', 'status': 'Printing'}}),
    ])
    client = FrappeQueueClient('https://x.test', 'k', 's', session=session)
    client.get('KPQ 1')
    client.update('KPQ 1', {'status': 'Printing'})
    assert session.calls[0][1].endswith('/api/resource/Kitchen%20Print%20Queue/KPQ%201')
    assert session.calls[1][0] == 'PUT'
    assert session.calls[1][2]['json'] == {'status': 'Printing'}


def test_401_raises_authentication_error():
    session = FakeSession([FakeResponse(status_code=401, text='unauthorized')])
    client = FrappeQueueClient('https://x.test', 'bad', 'bad', session=session)
    with pytest.raises(AuthenticationError, match='401'):
        client.list_retryable(3)
