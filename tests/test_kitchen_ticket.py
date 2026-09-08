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
    assert 'BBQ &lt;Counter&gt;' in html
    assert 'Table &lt;07&gt;' in html
    assert 'Pork &amp; &lt;Spicy&gt;' in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '999999' not in html
    assert '5000' not in html
    assert 'Grand Total' not in html
