from __future__ import annotations

import time

from cashier_queue_worker import (
    process_cashier_queue_record,
    recover_stale_cashier_printing,
)
from frappe_queue_client import AuthenticationError, FrappeQueueClient
from queue_worker import get_local_printers, load_config



def _require_config(config: dict, *keys: str) -> None:
    missing = [key for key in keys if not str(config.get(key) or '').strip()]
    if missing:
        raise ValueError('Missing config values: ' + ', '.join(missing))


def run_cashier_worker(config_path: str = 'config.json') -> None:
    config = load_config(config_path)
    _require_config(
        config,
        'FRAPPE_BASE_URL',
        'API_KEY',
        'API_SECRET',
        'SUMATRA_PDF_PATH',
    )
    if not str(config.get('EDGE_PATH') or config.get('WKHTMLTOPDF') or '').strip():
        raise ValueError('Missing PDF renderer: set EDGE_PATH or WKHTMLTOPDF')

    interval = max(1, int(config.get('POLL_INTERVAL_SECONDS', 3)))
    max_retries = max(1, int(config.get('MAX_PRINT_RETRIES', 3)))
    stale_seconds = max(30, int(config.get('STALE_PRINTING_SECONDS', 120)))

    client = FrappeQueueClient(
        str(config['FRAPPE_BASE_URL']),
        str(config['API_KEY']),
        str(config['API_SECRET']),
    )

    print('=' * 60)
    print(' Cashier Print Queue Worker')
    print(f" Site: {str(config['FRAPPE_BASE_URL']).rstrip('/')}")
    print(f' Poll: {interval}s | Max retries: {max_retries}')
    print('=' * 60)

    try:
        recovered = recover_stale_cashier_printing(client, stale_seconds)
        if recovered:
            print(f'[CASHIER] Recovered {recovered} stale Printing job(s).')
    except AuthenticationError as exc:
        print(f'[AUTH] {exc}')

    while True:
        try:
            installed_printers = get_local_printers()
            rows = client.list_cashier_retryable(max_retries)
            if rows:
                print(f'[CASHIER] Found {len(rows)} retryable job(s).')
            for queue in rows:
                print(
                    f"[CASHIER] {queue.get('name')} | {queue.get('sales_invoice')} "
                    f"-> {queue.get('printer_name')}"
                )
                process_cashier_queue_record(
                    client,
                    queue,
                    config,
                    installed_printers,
                )
        except AuthenticationError as exc:
            print(f'[AUTH] {exc}')
        except KeyboardInterrupt:
            print('\n[CASHIER] Stopped.')
            return
        except Exception as exc:
            print(f'[CASHIER] Poll failed: {exc}')
        time.sleep(interval)


if __name__ == '__main__':
    run_cashier_worker()
