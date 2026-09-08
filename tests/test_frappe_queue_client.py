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


def test_list_unqueued_sales_orders_uses_draft_and_queue_marker_filters():
    session = FakeSession([FakeResponse(payload={'data': [{'name': 'SAL-ORD-1'}]})])
    client = FrappeQueueClient('https://x.test', 'k', 's', session=session)

    rows = client.list_unqueued_sales_orders()

    assert rows == [{'name': 'SAL-ORD-1'}]
    method, url, kwargs = session.calls[0]
    assert method == 'GET'
    assert url.endswith('/api/resource/Sales%20Order')
    filters = json.loads(kwargs['params']['filters'])
    assert ['Sales Order', 'docstatus', '=', 0] in filters
    assert ['Sales Order', 'custom_kitchen_queue_created', '=', 0] in filters


def test_queue_discovery_client_helpers_use_standard_rest_resources():
    session = FakeSession([
        FakeResponse(payload={'data': [{'name': 'KPQ-1'}]}),
        FakeResponse(payload={'data': {'name': 'KPQ-2'}}),
        FakeResponse(payload={'data': {'name': 'BQ00011', 'item_group': 'Barbecue'}}),
        FakeResponse(payload={'data': {'name': 'Barbecue', 'custom_printer_name': 'Kitchen Printer'}}),
        FakeResponse(payload={'data': {'name': 'SAL-ORD-1', 'custom_kitchen_queue_created': 1}}),
    ])
    client = FrappeQueueClient('https://x.test', 'k', 's', session=session)

    assert client.queue_exists('SAL-ORD-1|Barbecue') is True
    created = client.create_queue({'sales_order': 'SAL-ORD-1'})
    item = client.get_item('BQ00011')
    counter = client.get_kitchen_counter('Barbecue')
    marked = client.mark_sales_order_queued('SAL-ORD-1')

    assert created['name'] == 'KPQ-2'
    assert item['item_group'] == 'Barbecue'
    assert counter['custom_printer_name'] == 'Kitchen Printer'
    assert marked['custom_kitchen_queue_created'] == 1
    assert session.calls[1][0] == 'POST'
    assert session.calls[1][1].endswith('/api/resource/Kitchen%20Print%20Queue')
    assert session.calls[4][0] == 'PUT'
    assert session.calls[4][2]['json'] == {'custom_kitchen_queue_created': 1}
