from __future__ import annotations

import html as html_module
import os
import tempfile

import pdfkit


def _esc(value) -> str:
    return html_module.escape(str(value or ''), quote=True)


def build_ticket_html(queue: dict, sales_order: dict, items: list[dict], restaurant_name: str) -> str:
    item_rows = []
    for item in items:
        note = str(item.get('note') or '').strip()
        note_html = f'<div class="note">Note: {_esc(note)}</div>' if note else ''
        item_rows.append(
            '<div class="item">'
            f'<div class="item-line"><span class="qty">{_esc(item.get("qty"))} x</span> '
            f'<span class="name">{_esc(item.get("item_name") or item.get("item_code"))}</span></div>'
            f'<div class="meta">{_esc(item.get("item_code"))} | {_esc(item.get("uom"))}</div>'
            f'{note_html}'
            '</div>'
        )

    order_time = sales_order.get('creation') or sales_order.get('transaction_date') or ''
    return f'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
@page {{ size: 80mm auto; margin: 2mm; }}
body {{ font-family: Arial, sans-serif; font-size: 12px; margin: 0; padding: 0; }}
.center {{ text-align: center; }}
.restaurant {{ font-size: 15px; font-weight: 700; }}
.counter {{ font-size: 17px; font-weight: 700; margin-top: 3px; }}
.rule {{ border-top: 1px dashed #000; margin: 5px 0; }}
.info {{ margin: 2px 0; }}
.item {{ margin: 7px 0; }}
.item-line {{ font-size: 15px; font-weight: 700; }}
.qty {{ display: inline-block; min-width: 38px; }}
.meta {{ font-size: 10px; margin-top: 2px; }}
.note {{ font-size: 12px; font-weight: 700; margin-top: 2px; }}
</style>
</head>
<body>
<div class="center restaurant">{_esc(restaurant_name)}</div>
<div class="center counter">{_esc(queue.get('kitchen_counter'))}</div>
<div class="rule"></div>
<div class="info"><b>Order:</b> {_esc(queue.get('sales_order'))}</div>
<div class="info"><b>Table/Customer:</b> {_esc(sales_order.get('customer'))}</div>
<div class="info"><b>Time:</b> {_esc(order_time)}</div>
<div class="rule"></div>
{''.join(item_rows)}
<div class="rule"></div>
</body>
</html>'''


def render_html_to_pdf(html: str, wkhtmltopdf_path: str) -> str:
    fd, pdf_path = tempfile.mkstemp(prefix='kitchen_ticket_', suffix='.pdf')
    os.close(fd)
    configuration = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
    options = {
        'page-width': '80mm',
        'margin-top': '2mm',
        'margin-right': '2mm',
        'margin-bottom': '2mm',
        'margin-left': '2mm',
        'encoding': 'UTF-8',
        'quiet': '',
    }
    try:
        pdfkit.from_string(html, pdf_path, configuration=configuration, options=options)
    except Exception:
        try:
            os.remove(pdf_path)
        except OSError:
            pass
        raise
    return pdf_path
