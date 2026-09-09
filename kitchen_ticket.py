from __future__ import annotations

from datetime import datetime
import html as html_module
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pdfkit


def _esc(value) -> str:
    return html_module.escape(str(value or ''), quote=True)


def _format_qty(value) -> str:
    try:
        return f'{float(value or 0):.0f}'
    except (TypeError, ValueError):
        return str(value or '')


def _format_order_datetime(value) -> str:
    text = str(value or '').strip()
    if not text:
        return ''

    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        return parsed.strftime('%d/%m/%Y %H:%M:%S')
    except ValueError:
        return text


def build_ticket_html(queue: dict, sales_order: dict, items: list[dict], restaurant_name: str) -> str:
    item_rows = []
    for item in items:
        note = str(item.get('note') or '').strip()
        note_html = f'<div class="note">Remark: {_esc(note)}</div>' if note else ''
        qty_uom = (
            f'{_format_qty(item.get("qty"))} {_esc(item.get("uom") or "")}'
        ).strip()
        item_rows.append(
            '<div class="item">'
            f'<div class="item-line"><span class="qty">{qty_uom} x</span> '
            f'<span class="name">{_esc(item.get("item_name") or item.get("item_code"))}</span></div>'
            f'{note_html}'
            '</div>'
        )

    order_time = sales_order.get('creation') or sales_order.get('transaction_date') or ''
    formatted_order_time = _format_order_datetime(order_time)
    table_name = _esc(sales_order.get('customer'))

    return f'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
@page {{ size: 80mm auto; margin: 0 2mm 1mm 2mm; }}
body {{ font-family: Arial, sans-serif; font-size: 12px; margin: 0; padding: 0; }}
.rule {{ border-top: 1px dashed #000; margin: 2px 0; }}
.ticket-head {{ margin: 1px 0; white-space: nowrap; }}
.table-name {{ font-size: 13px; font-weight: 700; }}
.order-datetime {{ font-size: 12px; margin-left: 10px; white-space: nowrap; }}
.item {{ margin: 3px 0; }}
.item-line {{ font-size: 12px; font-weight: 700; }}
.qty {{ display: inline-block; }}
.note {{ font-size: 12px; font-weight: 400; margin-top: 1px; }}
</style>
</head>
<body>
<div class="rule"></div>
<div class="ticket-head"><span class="table-name">{table_name}</span><span class="order-datetime">{_esc(formatted_order_time)}</span></div>
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
        'margin-top': '0mm',
        'margin-right': '2mm',
        'margin-bottom': '1mm',
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


def render_html_to_pdf_edge(html: str, edge_path: str, timeout_seconds: float = 15.0) -> str:
    """Render a local kitchen ticket with Microsoft Edge headless mode.

    Edge can return control before the child renderer finishes writing the PDF,
    so this waits until a non-empty PDF actually exists before returning it.
    """
    work_dir = tempfile.mkdtemp(prefix='kitchen_edge_')
    html_path = os.path.join(work_dir, 'ticket.html')
    pdf_path = os.path.join(work_dir, 'ticket.pdf')
    profile_dir = os.path.join(work_dir, 'profile')

    with open(html_path, 'w', encoding='utf-8') as fh:
        fh.write(html)

    command = [
        edge_path,
        '--headless',
        '--disable-gpu',
        '--no-pdf-header-footer',
        f'--user-data-dir={profile_dir}',
        f'--print-to-pdf={pdf_path}',
        Path(html_path).resolve().as_uri(),
    ]

    try:
        subprocess.run(command, check=True)
        deadline = time.monotonic() + float(timeout_seconds)
        while time.monotonic() < deadline:
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                return pdf_path
            time.sleep(0.05)
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    shutil.rmtree(work_dir, ignore_errors=True)
    raise RuntimeError('Microsoft Edge did not create the kitchen ticket PDF before timeout')
