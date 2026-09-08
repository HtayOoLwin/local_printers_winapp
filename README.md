# Local Printers Windows App

Windows middleware for silent printing from ERPNext/Frappe.

This repository now supports two modes:

1. **Kitchen Print Queue mode (recommended for the restaurant project)** — no `local_printers` Frappe app required. The Windows worker polls a custom ERPNext `Kitchen Print Queue` DocType through standard REST APIs and prints counter-specific tickets.
2. **Legacy Socket.IO mode** — keeps the existing `socket_app.py` flow for sites that have the `local_printers` Frappe app installed.

## Kitchen Print Queue Mode

Target site: `https://ourcity.s.frappe.cloud`

The restaurant mobile app creates a Sales Order. An ERPNext Sales Order `After Insert` Server Script groups items by Kitchen Counter and creates one durable queue row per counter. `queue_worker.py` polls those rows, validates the exact Windows printer name, renders an 80mm kitchen ticket locally, prints through SumatraPDF, and updates the queue status.

```text
Mobile Order
  -> Draft Sales Order
  -> After Insert Server Script
  -> Kitchen Print Queue (one row per counter)
  -> queue_worker.py
  -> Windows printer configured on Kitchen Counter
```

### ERPNext setup

Follow:

`docs/ERPNext_Kitchen_Print_Queue_Setup.md`

The paste-ready Sales Order Server Script is:

`erpnext/kitchen_print_queue_server_script.py`

### Windows configuration

Copy `config copy.json` to `config.json` and enter real values:

```json
{
  "FRAPPE_BASE_URL": "https://ourcity.s.frappe.cloud",
  "API_KEY": "your-api-key",
  "API_SECRET": "your-api-secret",
  "WKHTMLTOPDF": "C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe",
  "SUMATRA_PDF_PATH": "C:\\Program Files\\SumatraPDF\\SumatraPDF.exe",
  "POLL_INTERVAL_SECONDS": 3,
  "MAX_PRINT_RETRIES": 3,
  "STALE_PRINTING_SECONDS": 120,
  "RESTAURANT_NAME": "Doh Myot Daw BBQ & Restaurant"
}
```

Use the actual executable paths on the restaurant Windows PC. API credentials must belong to a user on `ourcity.s.frappe.cloud` with Read + Write permission on `Kitchen Print Queue` and Read permission on Sales Order.

### Run kitchen worker

```powershell
.\.venv\Scripts\python.exe queue_worker.py
```

or run:

```text
run_queue.bat
```

A normal lifecycle is:

```text
Pending -> Printing -> Printed
                   \-> Error
```

The worker retries Error rows up to `MAX_PRINT_RETRIES`. Jobs left in `Printing` after a worker restart are recovered after `STALE_PRINTING_SECONDS`.

## Prerequisites

- Windows
- Python 3.10+
- dependencies from `requirements.txt`
- wkhtmltopdf
- SumatraPDF
- Windows printers installed and visible through `Get-Printer`

Install Python dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Legacy Socket.IO Mode

`socket_app.py` remains available for older deployments that use the `local_printers` Frappe app. It connects to Frappe Socket.IO, listens for `document_print_event` / `sales_invoice_submitted`, and prints server-rendered PDFs through `printer_handlers.py`.

Run it with:

```powershell
.\.venv\Scripts\python.exe socket_app.py
```

The legacy mode still requires its original Socket.IO/login configuration and the server-side `local_printers` integration.

## Troubleshooting

| Issue | Check |
|---|---|
| `401 AuthenticationError` | API Key / Secret must be generated on the target ERPNext site (`ourcity.s.frappe.cloud`) |
| Queue stays Pending | Windows queue worker is not running or cannot read the queue |
| Queue becomes Error: printer not installed | `printer_name` must exactly match `Get-Printer` output |
| PDF rendering failure | Verify `WKHTMLTOPDF` executable path |
| SumatraPDF failure | Verify `SUMATRA_PDF_PATH` and printer status |
| No queue row after mobile order | Check Sales Order After Insert Server Script and item Kitchen Counter mapping |

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

## License

MIT License
