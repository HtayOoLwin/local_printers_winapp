# Kitchen Print Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows kitchen-print queue worker that polls standard ERPNext REST resources and prints counter-specific tickets without requiring the `local_printers` Frappe app.

**Architecture:** ERPNext creates one `Kitchen Print Queue` row per Sales Order / Kitchen Counter using a DocType Event Server Script. The Windows worker polls `Pending`/retryable `Error` rows, claims one row by setting `Printing`, renders a local kitchen-ticket PDF, validates the exact Windows printer name, prints through SumatraPDF, then persists `Printed` or `Error` state through standard Frappe REST APIs.

**Tech Stack:** Python 3.10+, requests, pywin32, pdfkit/wkhtmltopdf, SumatraPDF, pytest/unittest-style pure Python tests.

**Spec:** `docs/superpowers/specs/2026-09-08-kitchen-print-queue-design.md`

## Global Constraints

- Do not require the `local_printers` Frappe app.
- Do not merge or depend on `local_printers` master.
- One Windows PC owns all kitchen printers.
- ERPNext site is `https://ourcity.s.frappe.cloud`.
- Restaurant branch remains `bcn-restaurant-mobile-without-kitchen-monitor`.
- Printer names must exactly match Windows printer names.
- Standard Frappe REST only, authenticated with API key/secret.
- Phase 1 handles first-print tickets for newly inserted draft Sales Orders only.

---

### Task 1: Queue domain helpers

**Files:**
- Create: `kitchen_queue.py`
- Create: `tests/test_kitchen_queue.py`

**Interfaces:**
- Produces `parse_items_json(items_json: str) -> list[dict]`
- Produces `is_retryable(queue: dict, max_retries: int) -> bool`
- Produces `validate_printer_name(printer_name: str, installed_printers: list[str]) -> None`
- Produces `build_error_update(queue: dict, exc: Exception, max_retries: int) -> dict`

- [ ] **Step 1: Write failing tests** covering malformed JSON, retry count `< max`, exact printer match, missing printer, and error status updates.
- [ ] **Step 2: Run** `python -m pytest tests/test_kitchen_queue.py -q` and confirm failures are caused by missing helpers.
- [ ] **Step 3: Implement minimal helpers** in `kitchen_queue.py` with no Windows-only imports.
- [ ] **Step 4: Run** `python -m pytest tests/test_kitchen_queue.py -q` and require zero failures.
- [ ] **Step 5: Commit** `test/feat: add kitchen queue domain helpers`.

### Task 2: Frappe REST queue client

**Files:**
- Create: `frappe_queue_client.py`
- Create: `tests/test_frappe_queue_client.py`

**Interfaces:**
- Produces class `FrappeQueueClient(base_url: str, api_key: str, api_secret: str, timeout: int = 30)`
- Produces `list_retryable(max_retries: int) -> list[dict]`
- Produces `get(name: str) -> dict`
- Produces `update(name: str, values: dict) -> dict`
- Produces `get_sales_order(name: str) -> dict`

- [ ] **Step 1: Write failing tests** for auth headers, queue filters, URL quoting, 401 handling, and PUT payload shape using a fake requests session.
- [ ] **Step 2: Run** `python -m pytest tests/test_frappe_queue_client.py -q` and confirm expected failures.
- [ ] **Step 3: Implement** standard `/api/resource/...` GET/PUT calls; surface HTTP 401 as a clear `AuthenticationError` exception.
- [ ] **Step 4: Run** the focused test file and require zero failures.
- [ ] **Step 5: Commit** `feat: add frappe kitchen queue rest client`.

### Task 3: Kitchen ticket rendering and physical print bridge

**Files:**
- Create: `kitchen_ticket.py`
- Modify: `printer_handlers.py`
- Create: `tests/test_kitchen_ticket.py`

**Interfaces:**
- Produces `build_ticket_html(queue: dict, sales_order: dict, items: list[dict], restaurant_name: str) -> str`
- Produces `render_html_to_pdf(html: str, wkhtmltopdf_path: str) -> str`
- Changes `print_pdf_silent(...) -> None` to raise on failure instead of swallowing exceptions, so queue state can reflect the real result.

- [ ] **Step 1: Write failing tests** for HTML escaping, no price/tax content, item notes, and `print_pdf_silent` failure propagation.
- [ ] **Step 2: Run** `python -m pytest tests/test_kitchen_ticket.py -q` and confirm expected failures.
- [ ] **Step 3: Implement** HTML rendering with `html.escape`, PDF creation through `pdfkit.configuration(wkhtmltopdf=...)`, and exception propagation in the print bridge.
- [ ] **Step 4: Run** focused tests and require zero failures.
- [ ] **Step 5: Commit** `feat: render and print local kitchen tickets`.

### Task 4: Polling worker and stale-job recovery

**Files:**
- Create: `queue_worker.py`
- Create: `tests/test_queue_worker.py`
- Create: `run_queue.bat`
- Modify: `config copy.json`

**Interfaces:**
- Produces `process_queue_record(client, queue, config, installed_printers) -> None`
- Produces `recover_stale_printing(client, stale_seconds: int) -> int`
- Produces `run_worker(config_path: str = "config.json") -> None`

- [ ] **Step 1: Write failing tests** for `Pending -> Printing -> Printed`, missing printer -> Error, renderer/print failure -> retry increment + Error, max retry skip, and stale Printing recovery.
- [ ] **Step 2: Run** `python -m pytest tests/test_queue_worker.py -q` and confirm expected failures.
- [ ] **Step 3: Implement** one-at-a-time polling, 3-second default interval, max retries default 3, startup stale recovery default 120 seconds, and high-visibility 401 logging.
- [ ] **Step 4: Update config template** with `FRAPPE_BASE_URL`, `API_KEY`, `API_SECRET`, `WKHTMLTOPDF`, `SUMATRA_PDF_PATH`, `POLL_INTERVAL_SECONDS`, `MAX_PRINT_RETRIES`, `STALE_PRINTING_SECONDS`, and `RESTAURANT_NAME`.
- [ ] **Step 5: Add `run_queue.bat`** invoking `.venv\Scripts\python.exe queue_worker.py`.
- [ ] **Step 6: Run** `python -m pytest tests -q` and require zero failures.
- [ ] **Step 7: Commit** `feat: add kitchen print queue worker`.

### Task 5: ERPNext setup artifacts for no-app deployment

**Files:**
- Create: `erpnext/kitchen_print_queue_server_script.py`
- Create: `docs/ERPNext_Kitchen_Print_Queue_Setup.md`

**Interfaces:**
- Server Script uses only `doc` and `frappe` globals available to Frappe Server Scripts; no imports.
- Creates one queue row per counter with unique `queue_key = <sales_order>|<counter>`.

- [ ] **Step 1: Write the exact paste-ready Server Script** that groups Sales Order Items by `custom_kitchen_counter`, falls back to Item, snapshots `custom_printer_name`, writes `items_json` via `frappe.as_json`, skips duplicate queue keys, and records visible diagnostics for missing counters.
- [ ] **Step 2: Write exact ERPNext UI setup instructions** for `Kitchen Counter.custom_printer_name`, `Kitchen Print Queue` fields, `queue_key` Unique, permissions, API integration user, and After Insert Server Script configuration.
- [ ] **Step 3: Self-review** script for Server Script sandbox compatibility: no imports, no local filesystem/network calls, no dependency on `local_printers`.
- [ ] **Step 4: Commit** `docs: add erpnext kitchen print queue setup`.

### Task 6: Verification and live rollout

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README** with queue-worker mode and keep legacy Socket.IO mode documented separately.
- [ ] **Step 2: Run full automated test suite** `python -m pytest tests -q`.
- [ ] **Step 3: On Windows, run** `Get-Printer | Format-Table Name,PrinterStatus,DriverName,PortName` and verify ERPNext counter printer names match exactly.
- [ ] **Step 4: Start** `run_queue.bat` and verify the worker authenticates to `ourcity.s.frappe.cloud` without 401.
- [ ] **Step 5: Live test one counter**: place a new mobile order, verify one queue row, one ticket, and `Printed` + `printed_at`.
- [ ] **Step 6: Live test two counters**: place an order containing two counters, verify two queue rows and each printer receives only its own items.
- [ ] **Step 7: Review changes task-by-task before any merge into another branch.**
