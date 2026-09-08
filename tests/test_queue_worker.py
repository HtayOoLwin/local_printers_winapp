from datetime import datetime, timedelta
import json

import queue_worker
from queue_worker import discover_sales_orders, process_queue_record, recover_stale_printing


class FakeClient:
    def __init__(self):
        self.updates = []
        self.sales_orders = {
            'SAL-ORD-1': {
                'name': 'SAL-ORD-1',
                'customer': 'Table 07',
                'creation': '2026-09-08 10:00:00',
            }
        }
        self.printing = []

    def update(self, name, values):
        self.updates.append((name, dict(values)))
        return {'name': name, **values}

    def get_sales_order(self, name):
        return self.sales_orders[name]

    def list_printing(self):
        return list(self.printing)


def queue_record(**overrides):
    row = {
        'name': 'KPQ-00001',
        'sales_order': 'SAL-ORD-1',
        'kitchen_counter': 'BBQ Counter',
        'printer_name': 'Kitchen Printer',
        'items_json': '[{"item_code":"BBQ-1","item_name":"Pork BBQ","qty":2,"uom":"Plate","note":""}]',
        'status': 'Pending',
        'retry_count': 0,
    }
    row.update(overrides)
    return row


def base_config():
    return {
        'MAX_PRINT_RETRIES': 3,
        'RESTAURANT_NAME': 'Doh Myot Daw BBQ & Restaurant',
        'WKHTMLTOPDF': 'wkhtmltopdf.exe',
        'SUMATRA_PDF_PATH': 'SumatraPDF.exe',
    }


def test_resolve_pdf_renderer_uses_edge_when_configured():
    renderer, path = queue_worker._resolve_pdf_renderer({
        'EDGE_PATH': r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
    })
    assert renderer.__name__ == 'render_html_to_pdf_edge'
    assert path.endswith('msedge.exe')


def test_resolve_pdf_renderer_keeps_wkhtmltopdf_compatibility():
    renderer, path = queue_worker._resolve_pdf_renderer({'WKHTMLTOPDF': 'wkhtmltopdf.exe'})
    assert renderer.__name__ == 'render_html_to_pdf'
    assert path == 'wkhtmltopdf.exe'


def test_pending_claims_prints_and_marks_printed(tmp_path):
    client = FakeClient()
    printed = []
    pdf = tmp_path / 'ticket.pdf'
    pdf.write_bytes(b'%PDF')

    def render(html, wkhtml):
        assert 'Pork BBQ' in html
        return str(pdf)

    def print_pdf(path, printer, sumatra):
        printed.append((path, printer, sumatra))

    process_queue_record(
        client,
        queue_record(),
        base_config(),
        ['Kitchen Printer'],
        render_pdf=render,
        print_pdf=print_pdf,
    )

    assert client.updates[0][1]['status'] == 'Printing'
    assert client.updates[-1][1]['status'] == 'Printed'
    assert client.updates[-1][1]['printed_at']
    assert printed[0][1] == 'Kitchen Printer'


def test_missing_printer_marks_error_and_increments_retry():
    client = FakeClient()
    process_queue_record(
        client,
        queue_record(),
        base_config(),
        ['POS-80C'],
        render_pdf=lambda *_: (_ for _ in ()).throw(AssertionError('must not render')),
        print_pdf=lambda *_: None,
    )
    assert client.updates[-1][1]['status'] == 'Error'
    assert client.updates[-1][1]['retry_count'] == 1
    assert 'not installed' in client.updates[-1][1]['last_error']


def test_print_failure_marks_error():
    client = FakeClient()
    process_queue_record(
        client,
        queue_record(retry_count=1),
        base_config(),
        ['Kitchen Printer'],
        render_pdf=lambda *_: 'ticket.pdf',
        print_pdf=lambda *_: (_ for _ in ()).throw(RuntimeError('printer offline')),
    )
    assert client.updates[-1][1] == {
        'status': 'Error',
        'retry_count': 2,
        'last_error': 'printer offline',
    }


def test_max_retry_record_is_skipped():
    client = FakeClient()
    process_queue_record(
        client,
        queue_record(status='Error', retry_count=3),
        base_config(),
        ['Kitchen Printer'],
        render_pdf=lambda *_: 'ticket.pdf',
        print_pdf=lambda *_: None,
    )
    assert client.updates == []


def test_recover_stale_printing_marks_old_jobs_error():
    client = FakeClient()
    old = (datetime.now() - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
    recent = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    client.printing = [
        {'name': 'KPQ-old', 'status': 'Printing', 'retry_count': 0, 'modified': old},
        {'name': 'KPQ-new', 'status': 'Printing', 'retry_count': 0, 'modified': recent},
    ]
    count = recover_stale_printing(client, stale_seconds=120)
    assert count == 1
    assert client.updates[0][0] == 'KPQ-old'
    assert client.updates[0][1]['status'] == 'Error'


def _snapshot(items):
    return json.dumps({'version': 1, 'items': items}, ensure_ascii=False, sort_keys=True)


class DiscoveryClient:
    def __init__(self, sales_order):
        self.sales_order = sales_order
        self.created = []
        self.marked = []
        self.state_updates = []

    def list_unqueued_sales_orders(self):
        return [{'name': self.sales_order['name']}]

    def list_draft_sales_orders(self):
        return [{
            'name': self.sales_order['name'],
            'creation': self.sales_order.get('creation'),
            'modified': self.sales_order.get('modified'),
            'custom_kitchen_queue_created': self.sales_order.get('custom_kitchen_queue_created', 0),
            'custom_kitchen_print_snapshot': self.sales_order.get('custom_kitchen_print_snapshot', ''),
        }]

    def get_sales_order(self, name):
        assert name == self.sales_order['name']
        return self.sales_order

    def get_item(self, item_code):
        groups = {
            'BQ00011': 'Barbecue',
            'K00037': 'Kitchen',
            'B00001': 'Bar',
        }
        return {
            'name': item_code,
            'item_group': groups[item_code],
            'custom_kitchen_counter': groups[item_code],
        }

    def get_kitchen_counter(self, counter):
        return {'name': counter, 'custom_printer_name': 'Kitchen Printer'}

    def queue_exists(self, queue_key):
        return any(row['queue_key'] == queue_key for row in self.created)

    def create_queue(self, payload):
        self.created.append(dict(payload))
        return {'name': f'KPQ-{len(self.created):05d}', **payload}

    def mark_sales_order_queued(self, name):
        self.marked.append(name)
        return {'name': name, 'custom_kitchen_queue_created': 1}

    def update_sales_order_kitchen_state(self, name, snapshot):
        self.state_updates.append((name, snapshot))
        self.sales_order['custom_kitchen_queue_created'] = 1
        self.sales_order['custom_kitchen_print_snapshot'] = snapshot
        return {
            'name': name,
            'custom_kitchen_queue_created': 1,
            'custom_kitchen_print_snapshot': snapshot,
        }


def test_discover_new_sales_order_builds_full_queue_by_counter_and_saves_snapshot():
    sales_order = {
        'name': 'SAL-ORD-1',
        'creation': '2026-09-08 10:00:00',
        'modified': '2026-09-08 10:00:01.123456',
        'custom_kitchen_queue_created': 0,
        'custom_kitchen_print_snapshot': '',
        'items': [
            {
                'item_code': 'BQ00011',
                'item_name': 'Black Chicken',
                'qty': 1,
                'uom': 'Plate',
                'stock_uom': 'Plate',
                'custom_kitchen_counter': '0',
                'custom_kitchen_note': 'No spicy',
            },
            {
                'item_code': 'K00037',
                'item_name': 'S.F 12',
                'qty': 2,
                'uom': 'Plate',
                'stock_uom': 'Plate',
                'custom_kitchen_counter': '',
                'custom_kitchen_note': '',
            },
        ],
    }
    client = DiscoveryClient(sales_order)

    count = discover_sales_orders(client)

    assert count == 2
    assert {row['kitchen_counter'] for row in client.created} == {'Barbecue', 'Kitchen'}
    barbecue = next(row for row in client.created if row['kitchen_counter'] == 'Barbecue')
    assert barbecue['printer_name'] == 'Kitchen Printer'
    assert barbecue['status'] == 'Pending'
    assert json.loads(barbecue['items_json'])[0]['note'] == 'No spicy'
    assert client.state_updates and client.state_updates[-1][0] == 'SAL-ORD-1'
    saved = json.loads(client.state_updates[-1][1])
    assert saved['version'] == 1
    assert {row['counter'] for row in saved['items']} == {'Barbecue', 'Kitchen'}


def test_existing_sales_order_qty_increase_prints_only_positive_delta():
    previous = _snapshot([
        {
            'counter': 'Kitchen',
            'item_code': 'K00037',
            'item_name': 'S.F 12',
            'qty': 1.0,
            'uom': 'Plate',
            'note': '',
        }
    ])
    sales_order = {
        'name': 'SAL-ORD-2',
        'creation': '2026-09-08 10:00:00',
        'modified': '2026-09-08 12:20:00.123456',
        'custom_kitchen_queue_created': 1,
        'custom_kitchen_print_snapshot': previous,
        'items': [
            {
                'item_code': 'K00037',
                'item_name': 'S.F 12',
                'qty': 3,
                'uom': 'Plate',
                'custom_kitchen_counter': 'Kitchen',
                'custom_kitchen_note': '',
            }
        ],
    }
    client = DiscoveryClient(sales_order)

    count = discover_sales_orders(client)

    assert count == 1
    delta_items = json.loads(client.created[0]['items_json'])
    assert len(delta_items) == 1
    assert delta_items[0]['item_code'] == 'K00037'
    assert delta_items[0]['qty'] == 2.0
    assert client.created[0]['queue_key'].startswith('SAL-ORD-2|Kitchen|')
    saved = json.loads(client.state_updates[-1][1])
    assert saved['items'][0]['qty'] == 3.0


def test_existing_sales_order_qty_decrease_updates_snapshot_without_printing():
    previous = _snapshot([
        {
            'counter': 'Kitchen',
            'item_code': 'K00037',
            'item_name': 'S.F 12',
            'qty': 3.0,
            'uom': 'Plate',
            'note': '',
        }
    ])
    sales_order = {
        'name': 'SAL-ORD-3',
        'modified': '2026-09-08 12:21:00.123456',
        'custom_kitchen_queue_created': 1,
        'custom_kitchen_print_snapshot': previous,
        'items': [
            {
                'item_code': 'K00037',
                'item_name': 'S.F 12',
                'qty': 2,
                'uom': 'Plate',
                'custom_kitchen_counter': 'Kitchen',
                'custom_kitchen_note': '',
            }
        ],
    }
    client = DiscoveryClient(sales_order)

    count = discover_sales_orders(client)

    assert count == 0
    assert client.created == []
    saved = json.loads(client.state_updates[-1][1])
    assert saved['items'][0]['qty'] == 2.0


def test_existing_marked_order_without_snapshot_is_baselined_without_reprint():
    sales_order = {
        'name': 'SAL-ORD-OLD',
        'modified': '2026-09-08 12:22:00.123456',
        'custom_kitchen_queue_created': 1,
        'custom_kitchen_print_snapshot': '',
        'items': [
            {
                'item_code': 'K00037',
                'item_name': 'S.F 12',
                'qty': 4,
                'uom': 'Plate',
                'custom_kitchen_counter': 'Kitchen',
                'custom_kitchen_note': '',
            }
        ],
    }
    client = DiscoveryClient(sales_order)

    count = discover_sales_orders(client)

    assert count == 0
    assert client.created == []
    assert client.state_updates and client.state_updates[-1][0] == 'SAL-ORD-OLD'
    saved = json.loads(client.state_updates[-1][1])
    assert saved['items'][0]['qty'] == 4.0
