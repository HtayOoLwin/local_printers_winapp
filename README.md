# Local Printers Windows App

Windows middleware for silent printing from ERPNext/Frappe.

This repository supports three compatible printing paths:

1. **Kitchen Print Queue mode (recommended for kitchen tickets)** — no `local_printers` Frappe app required. `queue_worker.py` polls the custom ERPNext `Kitchen Print Queue` DocType and prints counter-specific tickets.
2. **Cashier BCN Print Job polling** — no `local_printers` Frappe app required. `socket_app.py` token-polls OurCity for server-rendered cashier bill PDFs and reports Printed/Failed results.
3. **Legacy Socket.IO mode** — `socket_app.py` still supports the existing `document_print_event` and `sales_invoice_submitted` listeners when the legacy Socket.IO/login configuration is present.

## Kitchen Print Queue Mode

Target site: `https://ourcity.s.frappe.cloud`

The restaurant mobile app creates a Sales Order. An ERPNext Sales Order Server Script creates durable kitchen queue work. `queue_worker.py` polls those rows, validates the exact Windows printer name, renders an 80mm kitchen ticket locally, prints through SumatraPDF, and updates the queue status.

```text
Mobile Order
  -> Draft Sales Order
  -> Server Script / kitchen queue logic
  -> Kitchen Print Queue
  -> queue_worker.py
  -> Windows printer configured on Kitchen Counter
```

### ERPNext setup

Follow:

`docs/ERPNext_Kitchen_Print_Queue_Setup.md`

The paste-ready Sales Order Server Script is:

`erpnext/kitchen_print_queue_server_script.py`

## Cashier BCN Print Job Polling

Target site: `https://ourcity.s.frappe.cloud`

Cashier billing uses the durable `BCN Print Job` queue created on OurCity. The Windows client sends the locally installed printer names to `bcn_print_jobs`, claims at most one matching Pending job, prints the supplied `pdf_base64` once through SumatraPDF, then reports the terminal result to `bcn_print_job_result`.

```text
Waiter Request for Bill
  -> Draft Sales Invoice PDF snapshot
  -> BCN Print Job / Pending
  -> socket_app.py
  -> bcn_print_jobs / Processing
  -> SumatraPDF / Windows printer
  -> bcn_print_job_result / Printed or Failed
```

The API Key/API Secret must belong to a user with the `BCN Printer Client` role. Cashier polling uses token authentication and does not depend on `AUTH_DATA` login cookies.

Run cashier polling with:

```powershell
.\.venv\Scripts\python.exe socket_app.py
```

If `LOGIN_URL`, `AUTH_DATA`, and `FRAPPE_SOCKET_URL` are not configured, `socket_app.py` stays alive in **cashier polling only** mode. If those legacy settings are present, the same process also keeps the original Socket.IO listeners.

Cashier polling defaults to 2 seconds. A claimed job is physically printed once by the process. If the Printed/Failed result POST cannot be acknowledged, the client retries only the result and does not claim another job or physically print the same job again.

If the process exits after paper output but before the result is acknowledged, a later server claim poll after the timeout can mark that Processing job Failed with:

```text
Print result unknown after client timeout
```

The server does not automatically requeue or redeliver that ambiguous job. The cashier/operator decides whether to use manual Reprint. A manual Reprint can duplicate paper if the first physical print actually succeeded but its result was lost.

## Windows configuration

Copy `config copy.json` to `config.json` and enter real values:

```json
{
  "FRAPPE_BASE_URL": "https://ourcity.s.frappe.cloud",
  "API_KEY": "your-api-key",
  "API_SECRET": "your-api-secret",
  "EDGE_PATH": "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "SUMATRA_PDF_PATH": "C:\\Program Files\\SumatraPDF\\SumatraPDF.exe",
  "POLL_INTERVAL_SECONDS": 2,
  "MAX_PRINT_RETRIES": 3,
  "STALE_PRINTING_SECONDS": 120,
  "RESTAURANT_NAME": "Doh Myot Daw BBQ & Restaurant"
}
```

Use the actual executable paths on the restaurant Windows PC. Do not log, commit, or share real API secrets.

For kitchen printing, the API user needs the permissions required by `Kitchen Print Queue` and Sales Order. For cashier polling, the API user needs the `BCN Printer Client` role.

### Run kitchen worker

```powershell
.\.venv\Scripts\python.exe queue_worker.py
```

or run:

```text
run_queue.bat
```

A normal kitchen lifecycle is:

```text
Pending -> Printing -> Printed
                   \-> Error
```

The kitchen worker retries Error rows up to `MAX_PRINT_RETRIES`. Jobs left in `Printing` after a worker restart are recovered after `STALE_PRINTING_SECONDS`.

To run kitchen and cashier printing on the same PC, keep `queue_worker.py` and `socket_app.py` running at the same time.

## Prerequisites

- Windows
- Python 3.10+
- dependencies from `requirements.txt`
- Microsoft Edge for the current kitchen PDF renderer
- SumatraPDF
- Windows printers installed and visible through `Get-Printer`

Install Python dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Legacy Socket.IO Mode

`socket_app.py` keeps the original integration for deployments that use the `local_printers` Frappe app. It connects to Frappe Socket.IO, listens for `document_print_event` / `sales_invoice_submitted`, and prints server-rendered PDFs through `printer_handlers.py`.

Legacy Socket.IO additionally needs its original `LOGIN_URL`, `AUTH_DATA`, `FRAPPE_SOCKET_URL`, and optional `SOCKETIO_PATH` configuration.

## Troubleshooting

| Issue | Check |
|---|---|
| `401 AuthenticationError` | API Key / Secret must be generated on the target ERPNext site (`ourcity.s.frappe.cloud`) |
| Cashier API says `BCN Printer Client role is required` | Assign the `BCN Printer Client` role to the API user |
| BCN Print Job stays Pending | `socket_app.py` is not running, API call is failing, or the job printer name does not match any installed printer |
| Cashier job becomes Failed after timeout | Check whether paper printed before deciding to use manual Reprint |
| Kitchen queue stays Pending | `queue_worker.py` is not running or cannot read the kitchen queue |
| Queue reports printer not installed | `printer_name` must exactly match `Get-Printer` output |
| PDF rendering failure | Verify the configured renderer path |
| SumatraPDF failure | Verify `SUMATRA_PDF_PATH` and printer status |
| No kitchen queue row after mobile order | Check Sales Order Server Script and item Kitchen Counter mapping |

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m py_compile socket_app.py polling_client.py printer_handlers.py
```

## License

MIT License
