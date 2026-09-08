import json

from frappe_queue_client import FrappeQueueClient


class FakeResponse:
    def __init__(self, payload=None):
        self.status_code = 200
        self._payload = payload if payload is not None else {}
        self.text = ''
        self.ok = True

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_client_lists_all_draft_sales_orders_with_kitchen_state_fields():
    session = FakeSession([FakeResponse(payload={'data': [{'name': 'SAL-ORD-1'}]})])
    client = FrappeQueueClient('https://x.test', 'k', 's', session=session)

    method = getattr(client, 'list_draft_sales_orders', None)
    assert callable(method)
    rows = method()

    assert rows == [{'name': 'SAL-ORD-1'}]
    _, url, kwargs = session.calls[0]
    assert url.endswith('/api/resource/Sales%20Order')
    filters = json.loads(kwargs['params']['filters'])
    fields = json.loads(kwargs['params']['fields'])
    assert ['Sales Order', 'docstatus', '=', 0] in filters
    assert not any(row[1] == 'custom_kitchen_queue_created' for row in filters)
    assert 'modified' in fields
    assert 'custom_kitchen_queue_created' in fields
    assert 'custom_kitchen_print_snapshot' in fields


def test_client_updates_sales_order_marker_and_snapshot_together():
    session = FakeSession([
        FakeResponse(payload={'data': {
            'name': 'SAL-ORD-1',
            'custom_kitchen_queue_created': 1,
            'custom_kitchen_print_snapshot': '{"version":1,"items":[]}',
        }})
    ])
    client = FrappeQueueClient('https://x.test', 'k', 's', session=session)

    method = getattr(client, 'update_sales_order_kitchen_state', None)
    assert callable(method)
    result = method('SAL-ORD-1', '{"version":1,"items":[]}')

    assert result['custom_kitchen_queue_created'] == 1
    method_name, url, kwargs = session.calls[0]
    assert method_name == 'PUT'
    assert url.endswith('/api/resource/Sales%20Order/SAL-ORD-1')
    assert kwargs['json'] == {
        'custom_kitchen_queue_created': 1,
        'custom_kitchen_print_snapshot': '{"version":1,"items":[]}',
    }
