from __future__ import annotations

import os
from datetime import datetime
from typing import Callable

from cashier_ticket import build_cashier_bill_html
from kitchen_queue import build_error_update, is_retryable, validate_printer_name
from kitchen_ticket import render_html_to_pdf, render_html_to_pdf_edge



def _normalized(value) -> str:
    return str(value or '').strip()


def _resolve_pdf_renderer(config: dict):
    edge_path = _normalized(config.get('EDGE_PATH'))
    if edge_path:
        return render_html_to_pdf_edge, edge_path
    wkhtmltopdf_path = _normalized(config.get('WKHTMLTOPDF'))
    if wkhtmltopdf_path:
        return render_html_to_pdf, wkhtmltopdf_path
    raise ValueError('Missing PDF renderer: set EDGE_PATH or WKHTMLTOPDF')


def _default_print_pdf(pdf_path: str, printer_name: str, sumatra_pdf_path: str) -> None:
    from printer_handlers import print_pdf_silent

    print_pdf_silent(
        pdf_path,
        printer_name,
        sumatra_pdf_path,
        raise_on_error=True,
    )


def process_cashier_queue_record(
    client,
    queue: dict,
    config: dict,
    installed_printers: list[str],
    *,
    render_pdf: Callable[[str, str], str] | None = None,
    print_pdf: Callable[[str, str, str], None] | None = None,
) -> None:
    max_retries = int(config.get('MAX_PRINT_RETRIES', 3))
    if not is_retryable(queue, max_retries):
        return

    name = queue['name']
    pdf_path: str | None = None
    client.update_cashier_queue(name, {'status': 'Printing', 'last_error': ''})

    try:
        printer_name = _normalized(queue.get('printer_name'))
        validate_printer_name(printer_name, installed_printers)

        invoice = client.get_sales_invoice(queue['sales_invoice'])
        if int(invoice.get('docstatus') or 0) != 0:
            raise ValueError('Cashier auto print requires a Draft Sales Invoice')

        html = build_cashier_bill_html(
            invoice,
            str(config.get('RESTAURANT_NAME') or 'Restaurant'),
        )
        renderer, renderer_path = _resolve_pdf_renderer(config)
        renderer = render_pdf or renderer
        pdf_path = renderer(html, renderer_path)

        printer_func = print_pdf or _default_print_pdf
        printer_func(pdf_path, printer_name, str(config['SUMATRA_PDF_PATH']))

        client.update_cashier_queue(
            name,
            {
                'status': 'Printed',
                'printed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'last_error': '',
            },
        )
    except Exception as exc:
        client.update_cashier_queue(
            name,
            build_error_update(queue, exc, max_retries),
        )
    finally:
        if pdf_path:
            try:
                os.remove(pdf_path)
            except OSError:
                pass


def _parse_frappe_datetime(value: str) -> datetime:
    text = str(value or '').strip().replace('Z', '+00:00')
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def recover_stale_cashier_printing(client, stale_seconds: int = 120) -> int:
    now = datetime.now()
    recovered = 0
    for queue in client.list_cashier_printing():
        try:
            modified = _parse_frappe_datetime(queue.get('modified') or '')
        except (TypeError, ValueError):
            continue
        if (now - modified).total_seconds() <= int(stale_seconds):
            continue
        client.update_cashier_queue(
            queue['name'],
            {
                'status': 'Error',
                'last_error': 'Recovered stale cashier Printing state after Windows worker restart',
            },
        )
        recovered += 1
    return recovered
