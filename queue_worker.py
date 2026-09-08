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
from kitchen_ticket import build_ticket_html, render_html_to_pdf, render_html_to_pdf_edge


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


def _resolve_pdf_renderer(config: dict):
    edge_path = _normalized(config.get('EDGE_PATH'))
    if edge_path:
        return render_html_to_pdf_edge, edge_path

    wkhtmltopdf_path = _normalized(config.get('WKHTMLTOPDF'))
    if wkhtmltopdf_path:
        return render_html_to_pdf, wkhtmltopdf_path

    raise ValueError('Missing PDF renderer: set EDGE_PATH or WKHTMLTOPDF')


def _route_sales_order_items(client: FrappeQueueClient, sales_order: dict) -> list[dict]:
    """Return normalized kitchen snapshot rows, including currently unrouted items."""
    snapshot_items: list[dict] = []
    item_cache: dict[str, dict] = {}
    missing_items: list[str] = []

    for row in sales_order.get('items') or []:
        item_code = str(row.get('item_code') or '')
        counter = _normalized(row.get('custom_kitchen_counter'))

        if counter in {'', '0'} and item_code:
            if item_code not in item_cache:
                item_cache[item_code] = client.get_item(item_code)
            item = item_cache[item_code]
            counter = _normalized(item.get('custom_kitchen_counter'))
            if counter in {'', '0'}:
                item_group = _normalized(item.get('item_group'))
                if item_group in DEFAULT_GROUP_COUNTERS:
                    counter = item_group

        if counter in {'', '0'}:
            counter = ''
            missing_items.append(item_code)

        try:
            qty = float(row.get('qty') or 0)
        except (TypeError, ValueError):
            qty = 0.0

        snapshot_items.append(
            {
                'counter': counter,
                'item_code': item_code,
                'item_name': row.get('item_name') or item_code,
                'qty': qty,
                'uom': row.get('uom') or row.get('stock_uom') or '',
                'note': row.get('custom_kitchen_note') or '',
            }
        )

    if missing_items:
        log.warning(
            'Sales Order %s has item(s) without kitchen routing: %s',
            sales_order.get('name'),
            ', '.join(code for code in missing_items if code),
        )

    return snapshot_items


def _snapshot_json(items: list[dict]) -> str:
    return json.dumps(
        {'version': 1, 'items': items},
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )


def _parse_snapshot(value: str) -> list[dict] | None:
    text = _normalized(value)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get('version') != 1:
        return None
    items = payload.get('items')
    if not isinstance(items, list):
        return None
    return items


def _item_identity(item: dict) -> tuple[str, str, str]:
    """Quantity identity intentionally excludes note text.

    Editing a kitchen note must not make the existing quantity look newly ordered.
    The current note is still carried onto a real positive-quantity delta ticket.
    """
    return (
        _normalized(item.get('counter')),
        _normalized(item.get('item_code')),
        _normalized(item.get('uom')),
    )


def _aggregate_quantities(items: list[dict]) -> dict[tuple[str, str, str], float]:
    totals: dict[tuple[str, str, str], float] = {}
    for item in items:
        key = _item_identity(item)
        try:
            qty = float(item.get('qty') or 0)
        except (TypeError, ValueError):
            qty = 0.0
        totals[key] = totals.get(key, 0.0) + qty
    return totals


def _positive_delta_items(previous: list[dict], current: list[dict]) -> list[dict]:
    """Return only positive quantity increases, grouped by counter/item/uom."""
    previous_qty = _aggregate_quantities(previous)
    current_qty = _aggregate_quantities(current)
    representative: dict[tuple[str, str, str], dict] = {}
    for item in current:
        representative[_item_identity(item)] = item

    delta_items: list[dict] = []
    for key, qty in current_qty.items():
        counter = key[0]
        if not counter:
            continue
        increase = qty - previous_qty.get(key, 0.0)
        if increase <= 0:
            continue
        source = representative[key]
        delta_items.append(
            {
                'counter': counter,
                'item_code': source.get('item_code'),
                'item_name': source.get('item_name') or source.get('item_code'),
                'qty': float(increase),
                'uom': source.get('uom') or '',
                'note': source.get('note') or '',
            }
        )
    return delta_items


def _group_print_items(items: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in items:
        counter = _normalized(item.get('counter'))
        if not counter:
            continue
        grouped.setdefault(counter, []).append(
            {
                'item_code': item.get('item_code'),
                'item_name': item.get('item_name') or item.get('item_code'),
                'qty': item.get('qty'),
                'uom': item.get('uom') or '',
                'note': item.get('note') or '',
            }
        )
    return grouped


def _queue_version_token(sales_order: dict) -> str:
    modified = _normalized(sales_order.get('modified'))
    return modified or datetime.now().strftime('%Y%m%d%H%M%S%f')


def _create_counter_queues(
    client: FrappeQueueClient,
    sales_order: dict,
    counter_items: dict[str, list[dict]],
    *,
    delta: bool,
) -> int:
    created = 0
    version_token = _queue_version_token(sales_order)

    for counter, items in counter_items.items():
        queue_key = (
            f"{sales_order['name']}|{counter}|{version_token}"
            if delta
            else f"{sales_order['name']}|{counter}"
        )
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
        log.info(
            'Queued %s -> %s -> %s%s',
            sales_order['name'],
            counter,
            printer_name,
            ' (delta)' if delta else '',
        )

    return created


def _cache_modified(
    modified_cache: dict[str, str] | None,
    name: str,
    fallback_modified: str,
    update_result: dict | None = None,
) -> None:
    if modified_cache is None:
        return
    modified_cache[name] = (
        _normalized((update_result or {}).get('modified'))
        or _normalized(fallback_modified)
    )


def discover_sales_orders(
    client: FrappeQueueClient,
    *,
    modified_cache: dict[str, str] | None = None,
) -> int:
    """Queue full first prints and positive deltas for edited Draft Sales Orders."""
    created = 0

    for summary in client.list_draft_sales_orders():
        name = summary['name']
        summary_modified = _normalized(summary.get('modified'))
        if (
            modified_cache is not None
            and summary_modified
            and modified_cache.get(name) == summary_modified
        ):
            continue

        sales_order = client.get_sales_order(name)
        source_modified = _normalized(sales_order.get('modified')) or summary_modified
        current_items = _route_sales_order_items(client, sales_order)
        current_snapshot = _snapshot_json(current_items)
        marker = int(sales_order.get('custom_kitchen_queue_created') or 0)
        previous_items = _parse_snapshot(sales_order.get('custom_kitchen_print_snapshot') or '')

        if marker == 0:
            created += _create_counter_queues(
                client,
                sales_order,
                _group_print_items(current_items),
                delta=False,
            )
            result = client.update_sales_order_kitchen_state(name, current_snapshot)
            _cache_modified(modified_cache, name, source_modified, result)
            continue

        if previous_items is None:
            # Existing orders printed before delta tracking was deployed are baselined once.
            result = client.update_sales_order_kitchen_state(name, current_snapshot)
            _cache_modified(modified_cache, name, source_modified, result)
            log.info('Baselined kitchen snapshot for %s', name)
            continue

        previous_snapshot = _snapshot_json(previous_items)
        if previous_snapshot == current_snapshot:
            _cache_modified(modified_cache, name, source_modified)
            continue

        delta_items = _positive_delta_items(previous_items, current_items)
        if delta_items:
            created += _create_counter_queues(
                client,
                sales_order,
                _group_print_items(delta_items),
                delta=True,
            )

        # Decreases/removals/note-only edits intentionally do not print,
        # but still become the new baseline.
        result = client.update_sales_order_kitchen_state(name, current_snapshot)
        _cache_modified(modified_cache, name, source_modified, result)

    return created


def process_queue_record(
    client: FrappeQueueClient,
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
        renderer, renderer_path = _resolve_pdf_renderer(config)
        renderer = render_pdf or renderer
        pdf_path = renderer(html, renderer_path)
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
        'SUMATRA_PDF_PATH',
    )
    renderer, renderer_path = _resolve_pdf_renderer(config)
    interval = max(1, int(config.get('POLL_INTERVAL_SECONDS', 3)))
    max_retries = max(1, int(config.get('MAX_PRINT_RETRIES', 3)))
    stale_seconds = max(30, int(config.get('STALE_PRINTING_SECONDS', 120)))

    client = FrappeQueueClient(
        str(config['FRAPPE_BASE_URL']),
        str(config['API_KEY']),
        str(config['API_SECRET']),
    )
    modified_cache: dict[str, str] = {}

    print('=' * 60)
    print(' Kitchen Print Queue Worker')
    print(f" Site: {str(config['FRAPPE_BASE_URL']).rstrip('/')}")
    print(f' Renderer: {renderer.__name__} -> {renderer_path}')
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
            discovered = discover_sales_orders(client, modified_cache=modified_cache)
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
