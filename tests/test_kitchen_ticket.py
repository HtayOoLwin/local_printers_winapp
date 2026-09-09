import os
import subprocess
import threading
import time

import kitchen_ticket
from kitchen_ticket import build_ticket_html


def test_build_ticket_html_escapes_user_content_and_has_no_prices():
    queue = {
        'kitchen_counter': 'BBQ <Counter>',
        'sales_order': 'SAL-ORD-1',
    }
    sales_order = {
        'customer': 'Table <07>',
        'transaction_date': '2026-09-08',
        'creation': '2026-09-08 10:00:00',
        'grand_total': 999999,
    }
    items = [{
        'item_code': 'BBQ-001',
        'item_name': 'Pork & <Spicy>',
        'qty': 2,
        'uom': 'Plate',
        'note': '<script>alert(1)</script>',
        'rate': 5000,
    }]
    html = build_ticket_html(queue, sales_order, items, 'Doh Myot Daw BBQ & Restaurant')
    assert 'Table &lt;07&gt;' in html
    assert 'Pork &amp; &lt;Spicy&gt;' in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '999999' not in html
    assert '5000' not in html
    assert 'Grand Total' not in html


def test_build_ticket_html_uses_compact_waiter_layout():
    queue = {
        'kitchen_counter': 'Kitchen',
        'sales_order': 'SAL-ORD-2026-00011',
    }
    sales_order = {
        'customer': 'Table 08',
        'creation': '2026-09-08 12:09:27.778956',
    }
    items = [{
        'item_code': 'K00037',
        'item_name': 'S.F Chicken',
        'qty': 3.0,
        'uom': 'Nos',
        'note': 'Less spicy',
    }]

    html = build_ticket_html(queue, sales_order, items, 'Doh Myot Daw BBQ & Restaurant')

    assert 'Doh Myot Daw BBQ &amp; Restaurant' not in html
    assert '<div class="center counter">Kitchen</div>' not in html
    assert 'SAL-ORD-2026-00011' not in html
    assert 'Order:' not in html
    assert 'Table/Customer:' not in html
    assert 'Time:' not in html
    assert '<table class="ticket-head">' in html
    assert '<td class="table-name">Table 08</td>' in html
    assert '<td class="order-datetime">08/09/2026 12:09:27</td>' in html
    assert 'display: flex' not in html
    assert '.ticket-head { width: 100%; border-collapse: collapse;' in html
    assert '.table-name { font-size: 13px; font-weight: 700;' in html
    assert '.order-datetime { font-size: 12px; text-align: right; white-space: nowrap;' in html
    assert 'K00037' not in html
    assert '3.0' not in html
    assert '<span class="qty">3 Nos x</span>' in html
    assert '<span class="name">S.F Chicken</span>' in html
    assert 'Remark: Less spicy' in html
    assert '.item-line { font-size: 12px;' in html
    assert '.note { font-size: 12px;' in html
    assert '@page { size: 80mm auto; margin: 0 2mm 1mm 2mm; }' in html


def test_edge_renderer_waits_for_pdf_created_after_browser_process_returns(tmp_path, monkeypatch):
    edge = tmp_path / 'msedge.exe'
    edge.write_text('edge')
    writer_threads = []

    def fake_run(command, check):
        pdf_arg = next(arg for arg in command if arg.startswith('--print-to-pdf='))
        pdf_path = pdf_arg.split('=', 1)[1]

        def delayed_write():
            time.sleep(0.08)
            with open(pdf_path, 'wb') as fh:
                fh.write(b'%PDF-edge-test')

        thread = threading.Thread(target=delayed_write)
        thread.start()
        writer_threads.append(thread)

    monkeypatch.setattr(subprocess, 'run', fake_run)

    pdf_path = kitchen_ticket.render_html_to_pdf_edge(
        '<html><body>Kitchen</body></html>',
        str(edge),
        timeout_seconds=2,
    )

    for thread in writer_threads:
        thread.join()

    assert os.path.exists(pdf_path)
    assert open(pdf_path, 'rb').read() == b'%PDF-edge-test'
