from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from frappe_queue_client import AuthenticationError, FrappeQueueClient
from kitchen_queue import build_error_update, is_retryable, parse_items_json, validate_printer_name
from kitchen_ticket import build_ticket_html, render_html_to_pdf


LOG_DIR = Path(__file__).resolve().parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
log = logging.getLogger('queue_worker')
if not log.handlers:
    log.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    log.addHandler(stream)
    file_handler = logging.FileHandler(LOG_DIR / 'queue_worker.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)


DEFAULT_GROUP_COUNTERS = {'Bar', 'Barbecue', 'Kitchen'}


def load_config(config_path: str = 'config.json') -> dict:
    with open(config_path, 'r', encoding='utf-8-sig') as fh:
        return json.load(fh)


def get_local_printers() -> list[str]:
    import win32print

    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [row[2] for row in win32print.EnumPrinters(flags)]


def _default_print_pdf(pdf_path: str, printer_name: str, sumatra_pdf_path: str) -> None:
    from printer_handlers import print_pdf_silent

    print_pdf_silent(
        pdf_path,
        printer_name,
        sumatra_pdf_path,
        raise_on_error=True,
    )


def _normalized(value) -> str:
    return str(value or '').strip()


def discover_sales_orders(client: FrappeQueueClient) -> int:
    """Create durable queue rows for new Draft Sales Orders using only standard REST APIs."""
    created = 0
    for summary in client.list_unqueued_sales_orders():
        sales_order = client.get_sales_order(summary['name'])
        counter_items: dict[str, list[dict]] = {}
        item_cache: dict[str, dict] = {}
        missing_items: list[str] = []

        for row in sales_order.get('items') or []:
            counter = _normalized(row.get('custom_kitchen_counter'))
            if counter in {'', '0'}:
                item_code = str(row.get('item_code') or '')
                if item_code not in item_cache:
                    item_cache[item_code] = client.get_item(item_code)
                item = item_cache[item_code]
                counter = _normalized(item.get('custom_kitchen_counter'))
                if counter in {'', '0'}:
                    item_group = _normalized(item.get('item_group'))
                    if item_group in DEFAULT_GROUP_COUNTERS:
                        counter = item_group

            if counter in {'', '0'}:
                missing_items.append(str(row.get('item_code') or ''))
                continue

            counter_items.setdefault(counter, []).append(
                {
                    'item_code': row.get('item_code'),
                    'item_name': row.get('item_name') or row.get('item_code'),
                    'qty': row.get('qty'),
                    'uom': row.get('uom') or row.get('stock_uom'),
                    'note': row.get('custom_kitchen_note') or '',
                }
            )

        if missing_items:
            log.warning(
                'Sales Order %s has item(s) without kitchen routing: %s',
                sales_order.get('name'),
                ', '.join(missing_items),
            )

        for counter, items in counter_items.items():
            queue_key = f"{sales_order['name']}|{counter}"
            if client.queue_exists(queue_key):
                continue

            counter_doc = client.get_kitchen_counter(counter)
            printer_name = _normalized(counter_doc.get('custom_printer_name'))
            status = 'Pending' if printer_name else 'Error'
            last_error = (
                ''
                if printer_name
                else f'Printer not configured for Kitchen Counter {counter}'
            )
            client.create_queue(
                {
                    'sales_order': sales_order['name'],
                    'kitchen_counter': counter,
                    'printer_name': printer_name,
                    'items_json': json.dumps(items, ensure_ascii=False),
                    'print_format': 'Kitchen Ticket',
                    'status': status,
                    'retry_count': 0,
                    'last_error': last_error,
                    'queue_key': queue_key,
                }
            )
            created += 1
            log.info('Queued %s -> %s -> %s', sales_order['name'], counter, printer_name)

        client.mark_sales_order_queued(sales_order['name'])

    return created


def process_queue_record(
    client: FrappeQueueClient,
    queue: dict,
    config: dict,
    installed_printers: list[str],
    *,
    render_pdf: Callable[[str, str], str] = render_html_to_pdf,
    print_pdf: Callable[[str, str, str], None] | None = None,
) -> None:
    max_retries = int(config.get('MAX_PRINT_RETRIES', 3))
    if not is_retryable(queue, max_retries):
        return

    name = queue['name']
    pdf_path: str | None = None
    client.update(name, {'status': 'Printing', 'last_error': ''})

    try:
        items = parse_items_json(queue.get('items_json') or '')
        printer_name = str(queue.get('printer_name') or '')
        validate_printer_name(printer_name, installed_printers)

        sales_order = client.get_sales_order(queue['sales_order'])
        html = build_ticket_html(
            queue,
            sales_order,
            items,
            str(config.get('RESTAURANT_NAME') or 'Restaurant'),
        )
        pdf_path = render_pdf(html, str(config['WKHTMLTOPDF']))
        printer_func = print_pdf or _default_print_pdf
        printer_func(pdf_path, printer_name, str(config['SUMATRA_PDF_PATH']))

        client.update(
            name,
            {
                'status': 'Printed',
                'printed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'last_error': '',
            },
        )
        log.info('Printed %s -> %s', name, printer_name)
    except Exception as exc:
        client.update(name, build_error_update(queue, exc, max_retries))
        log.exception('Queue %s failed: %s', name, exc)
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


def recover_stale_printing(client: FrappeQueueClient, stale_seconds: int = 120) -> int:
    now = datetime.now()
    recovered = 0
    for queue in client.list_printing():
        try:
            modified = _parse_frappe_datetime(queue.get('modified') or '')
        except (TypeError, ValueError):
            continue
        if (now - modified).total_seconds() <= int(stale_seconds):
            continue
        client.update(
            queue['name'],
            {
                'status': 'Error',
                'last_error': 'Recovered stale Printing state after Windows worker restart',
            },
        )
        recovered += 1
    return recovered


def _require_config(config: dict, *keys: str) -> None:
    missing = [key for key in keys if not str(config.get(key) or '').strip()]
    if missing:
        raise ValueError('Missing config values: ' + ', '.join(missing))


def run_worker(config_path: str = 'config.json') -> None:
    config = load_config(config_path)
    _require_config(
        config,
        'FRAPPE_BASE_URL',
        'API_KEY',
        'API_SECRET',
        'WKHTMLTOPDF',
        'SUMATRA_PDF_PATH',
    )
    interval = max(1, int(config.get('POLL_INTERVAL_SECONDS', 3)))
    max_retries = max(1, int(config.get('MAX_PRINT_RETRIES', 3)))
    stale_seconds = max(30, int(config.get('STALE_PRINTING_SECONDS', 120)))

    client = FrappeQueueClient(
        str(config['FRAPPE_BASE_URL']),
        str(config['API_KEY']),
        str(config['API_SECRET']),
    )

    print('=' * 60)
    print(' Kitchen Print Queue Worker')
    print(f" Site: {str(config['FRAPPE_BASE_URL']).rstrip('/')}")
    print(f' Poll: {interval}s | Max retries: {max_retries}')
    print('=' * 60)

    try:
        recovered = recover_stale_printing(client, stale_seconds)
        if recovered:
            print(f'[QUEUE] Recovered {recovered} stale Printing job(s).')
    except AuthenticationError as exc:
        print(f'[AUTH] {exc}')
        log.error('%s', exc)

    while True:
        try:
            discovered = discover_sales_orders(client)
            if discovered:
                print(f'[QUEUE] Created {discovered} new kitchen queue job(s).')

            installed_printers = get_local_printers()
            rows = client.list_retryable(max_retries)
            if rows:
                print(f'[QUEUE] Found {len(rows)} retryable job(s).')
            for queue in rows:
                print(
                    f"[QUEUE] {queue.get('name')} | {queue.get('kitchen_counter')} "
                    f"-> {queue.get('printer_name')}"
                )
                process_queue_record(client, queue, config, installed_printers)
        except AuthenticationError as exc:
            print(f'[AUTH] {exc}')
            log.error('%s', exc)
        except KeyboardInterrupt:
            print('\n[QUEUE] Stopped.')
            return
        except Exception as exc:
            print(f'[QUEUE] Poll failed: {exc}')
            log.exception('Poll failed: %s', exc)
        time.sleep(interval)


if __name__ == '__main__':
    run_worker()
