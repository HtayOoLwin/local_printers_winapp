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


def test_discover_sales_orders_builds_queue_by_counter_and_marks_order_queued():
    class DiscoveryClient:
        def __init__(self):
            self.created = []
            self.marked = []

        def list_unqueued_sales_orders(self):
            return [{'name': 'SAL-ORD-1'}]

        def get_sales_order(self, name):
            assert name == 'SAL-ORD-1'
            return {
                'name': name,
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

        def get_item(self, item_code):
            if item_code == 'BQ00011':
                return {
                    'name': item_code,
                    'item_group': 'Barbecue',
                    'custom_kitchen_counter': '',
                }
            return {
                'name': item_code,
                'item_group': 'Kitchen',
                'custom_kitchen_counter': 'Kitchen',
            }

        def get_kitchen_counter(self, counter):
            return {'name': counter, 'custom_printer_name': 'Kitchen Printer'}

        def queue_exists(self, queue_key):
            return False

        def create_queue(self, payload):
            self.created.append(dict(payload))
            return {'name': f'KPQ-{len(self.created):05d}', **payload}

        def mark_sales_order_queued(self, name):
            self.marked.append(name)
            return {'name': name, 'custom_kitchen_queue_created': 1}

    client = DiscoveryClient()
    count = discover_sales_orders(client)

    assert count == 2
    assert client.marked == ['SAL-ORD-1']
    assert {row['kitchen_counter'] for row in client.created} == {'Barbecue', 'Kitchen'}
    assert {row['queue_key'] for row in client.created} == {
        'SAL-ORD-1|Barbecue',
        'SAL-ORD-1|Kitchen',
    }
    barbecue = next(row for row in client.created if row['kitchen_counter'] == 'Barbecue')
    assert barbecue['printer_name'] == 'Kitchen Printer'
    assert barbecue['status'] == 'Pending'
    assert json.loads(barbecue['items_json'])[0]['note'] == 'No spicy'
