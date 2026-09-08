from datetime import datetime, timedelta

from queue_worker import process_queue_record, recover_stale_printing


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
