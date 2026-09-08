import erpnext.setup_kitchen_print_queue as setup


def test_ensure_setup_creates_hidden_sales_order_print_snapshot_field():
    class FakeClient:
        def __init__(self):
            self.created = []

        def exists(self, doctype, name):
            return False

        def create(self, doctype, payload):
            self.created.append((doctype, payload))
            return payload

    client = FakeClient()
    setup.ensure_setup(client, 'Selling')

    snapshot_fields = [
        payload
        for doctype, payload in client.created
        if doctype == 'Custom Field'
        and payload.get('dt') == 'Sales Order'
        and payload.get('fieldname') == 'custom_kitchen_print_snapshot'
    ]

    assert len(snapshot_fields) == 1
    assert snapshot_fields[0]['fieldtype'] == 'Long Text'
    assert snapshot_fields[0]['hidden'] == 1
