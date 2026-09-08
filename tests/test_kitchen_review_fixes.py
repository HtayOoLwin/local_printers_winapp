import json

from queue_worker import discover_sales_orders


def _snapshot(items):
    return json.dumps({'version': 1, 'items': items}, ensure_ascii=False, sort_keys=True)


class ReviewFixClient:
    def __init__(self, sales_order):
        self.sales_order = sales_order
        self.created = []
        self.state_updates = []
        self.get_sales_order_calls = 0

    def list_draft_sales_orders(self):
        return [{
            'name': self.sales_order['name'],
            'creation': self.sales_order.get('creation'),
            'modified': self.sales_order.get('modified'),
            'custom_kitchen_queue_created': self.sales_order.get('custom_kitchen_queue_created', 0),
            'custom_kitchen_print_snapshot': self.sales_order.get('custom_kitchen_print_snapshot', ''),
        }]

    def get_sales_order(self, name):
        self.get_sales_order_calls += 1
        assert name == self.sales_order['name']
        return self.sales_order

    def get_item(self, item_code):
        return {
            'name': item_code,
            'item_group': 'Kitchen',
            'custom_kitchen_counter': 'Kitchen',
        }

    def get_kitchen_counter(self, counter):
        return {'name': counter, 'custom_printer_name': 'Kitchen Printer'}

    def queue_exists(self, queue_key):
        return any(row['queue_key'] == queue_key for row in self.created)

    def create_queue(self, payload):
        self.created.append(dict(payload))
        return {'name': f'KPQ-{len(self.created):05d}', **payload}

    def update_sales_order_kitchen_state(self, name, snapshot):
        self.state_updates.append((name, snapshot))
        self.sales_order['custom_kitchen_queue_created'] = 1
        self.sales_order['custom_kitchen_print_snapshot'] = snapshot
        return {
            'name': name,
            'custom_kitchen_queue_created': 1,
            'custom_kitchen_print_snapshot': snapshot,
        }


def test_note_only_edit_updates_snapshot_without_reprinting_quantity():
    previous = _snapshot([
        {
            'counter': 'Kitchen',
            'item_code': 'K00037',
            'item_name': 'S.F 12',
            'qty': 3.0,
            'uom': 'Nos',
            'note': '',
        }
    ])
    sales_order = {
        'name': 'SAL-ORD-NOTE',
        'modified': '2026-09-08 13:20:00.000001',
        'custom_kitchen_queue_created': 1,
        'custom_kitchen_print_snapshot': previous,
        'items': [
            {
                'item_code': 'K00037',
                'item_name': 'S.F 12',
                'qty': 3,
                'uom': 'Nos',
                'custom_kitchen_counter': 'Kitchen',
                'custom_kitchen_note': 'No spicy',
            }
        ],
    }
    client = ReviewFixClient(sales_order)

    count = discover_sales_orders(client)

    assert count == 0
    assert client.created == []
    saved = json.loads(client.state_updates[-1][1])
    assert saved['items'][0]['note'] == 'No spicy'
    assert saved['items'][0]['qty'] == 3.0


def test_qty_increase_with_note_change_prints_only_delta_using_current_note():
    previous = _snapshot([
        {
            'counter': 'Kitchen',
            'item_code': 'K00037',
            'item_name': 'S.F 12',
            'qty': 1.0,
            'uom': 'Nos',
            'note': '',
        }
    ])
    sales_order = {
        'name': 'SAL-ORD-NOTE-QTY',
        'modified': '2026-09-08 13:21:00.000001',
        'custom_kitchen_queue_created': 1,
        'custom_kitchen_print_snapshot': previous,
        'items': [
            {
                'item_code': 'K00037',
                'item_name': 'S.F 12',
                'qty': 3,
                'uom': 'Nos',
                'custom_kitchen_counter': 'Kitchen',
                'custom_kitchen_note': 'No spicy',
            }
        ],
    }
    client = ReviewFixClient(sales_order)

    count = discover_sales_orders(client)

    assert count == 1
    delta = json.loads(client.created[0]['items_json'])
    assert delta == [{
        'item_code': 'K00037',
        'item_name': 'S.F 12',
        'qty': 2.0,
        'uom': 'Nos',
        'note': 'No spicy',
    }]


def test_modified_cache_skips_full_fetch_for_unchanged_order():
    current_items = [
        {
            'counter': 'Kitchen',
            'item_code': 'K00037',
            'item_name': 'S.F 12',
            'qty': 3.0,
            'uom': 'Nos',
            'note': '',
        }
    ]
    sales_order = {
        'name': 'SAL-ORD-CACHE',
        'modified': '2026-09-08 13:22:00.000001',
        'custom_kitchen_queue_created': 1,
        'custom_kitchen_print_snapshot': _snapshot(current_items),
        'items': [
            {
                'item_code': 'K00037',
                'item_name': 'S.F 12',
                'qty': 3,
                'uom': 'Nos',
                'custom_kitchen_counter': 'Kitchen',
                'custom_kitchen_note': '',
            }
        ],
    }
    client = ReviewFixClient(sales_order)
    modified_cache = {}

    assert discover_sales_orders(client, modified_cache=modified_cache) == 0
    assert client.get_sales_order_calls == 1

    assert discover_sales_orders(client, modified_cache=modified_cache) == 0
    assert client.get_sales_order_calls == 1

    client.sales_order['modified'] = '2026-09-08 13:23:00.000001'
    assert discover_sales_orders(client, modified_cache=modified_cache) == 0
    assert client.get_sales_order_calls == 2
