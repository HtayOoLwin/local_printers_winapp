# ERPNext Kitchen Print Queue Setup (No Frappe App Required)

This setup is for `https://ourcity.s.frappe.cloud` and does **not** require installing the `local_printers` Frappe app or using a Frappe Server Script.

The Windows worker polls new Draft Sales Orders through the standard Frappe REST API, creates durable `Kitchen Print Queue` rows, and then prints them locally.

## 1. Automated ERPNext setup

From the Windows app folder, run:

```powershell
cd C:\Users\htayoolwin\local_printers_winapp_code
.\.venv\Scripts\python.exe .\erpnext\setup_kitchen_print_queue.py --printer "Kitchen Printer"
```

The setup is idempotent and creates or verifies:

- Custom DocType `Kitchen Counter`
- Custom DocType `Kitchen Print Queue`
- `Item.custom_kitchen_counter`
- existing `Sales Order Item.custom_kitchen_counter`
- existing `Sales Order Item.custom_kitchen_note`
- hidden Check field `Sales Order.custom_kitchen_queue_created`
- Kitchen Counter records `Bar`, `Barbecue`, and `Kitchen`
- Item routing for Item Groups `Bar`, `Barbecue`, and `Kitchen`

When the Sales Order queue marker is first created, existing Draft Sales Orders are marked as already seen so old orders are not printed when the worker starts.

## 2. Printer assignment is user-editable

Open **Kitchen Counter** in ERPNext. Each record has:

```text
Printer Name
```

The value must exactly match a printer returned by Windows `Get-Printer`.

For the current one-printer test, all three counters can use:

```text
Bar       -> Kitchen Printer
Barbecue  -> Kitchen Printer
Kitchen   -> Kitchen Printer
```

Later, when three physical printers are available, users can change only the ERPNext values, for example:

```text
Bar       -> Bar Printer
Barbecue  -> BBQ Printer
Kitchen   -> Kitchen Printer
```

No mobile-app or Windows-worker code change is required when printer assignments change.

## 3. Queue routing

For each new Draft Sales Order the Windows worker resolves the counter in this order:

1. `Sales Order Item.custom_kitchen_counter`
2. `Item.custom_kitchen_counter`
3. Item Group fallback for `Bar`, `Barbecue`, or `Kitchen`

The worker treats blank and `0` as missing routing values.

One `Kitchen Print Queue` row is created per Kitchen Counter with unique key:

```text
<Sales Order>|<Kitchen Counter>
```

This prevents duplicate queue rows if the worker retries discovery.

## 4. Permissions

The API user used by the Windows worker needs:

- `Kitchen Print Queue`: Read + Write + Create
- `Kitchen Counter`: Read
- `Sales Order`: Read + Write (Write is required only for `custom_kitchen_queue_created`)
- `Item`: Read

For initial testing, a System Manager API user is the fastest option.

Do not paste the API Secret into chat. Keep it only in the local `config.json`.

## 5. Windows config

The worker needs these values in `config.json`:

```json
{
  "FRAPPE_BASE_URL": "https://ourcity.s.frappe.cloud",
  "API_KEY": "YOUR_OURCITY_API_KEY",
  "API_SECRET": "YOUR_OURCITY_API_SECRET",
  "WKHTMLTOPDF": "C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe",
  "SUMATRA_PDF_PATH": "C:\\Program Files\\SumatraPDF\\SumatraPDF.exe",
  "POLL_INTERVAL_SECONDS": 3,
  "MAX_PRINT_RETRIES": 3,
  "STALE_PRINTING_SECONDS": 120,
  "RESTAURANT_NAME": "Doh Myot Daw BBQ & Restaurant"
}
```

If SumatraPDF or wkhtmltopdf is installed elsewhere, use the actual path.

## 6. Start the worker

```powershell
cd C:\Users\htayoolwin\local_printers_winapp_code
.\.venv\Scripts\python.exe queue_worker.py
```

Expected startup output includes:

```text
Kitchen Print Queue Worker
Site: https://ourcity.s.frappe.cloud
Poll: 3s | Max retries: 3
```

The worker then repeatedly:

1. finds new Draft Sales Orders where `custom_kitchen_queue_created = 0`
2. creates one queue row per counter
3. marks the Sales Order queue marker as complete
4. prints retryable queue rows

## 7. First live test

1. Keep `queue_worker.py` running.
2. Place a new order from the mobile app.
3. Confirm the new Sales Order remains Draft.
4. Open `Kitchen Print Queue` in ERPNext.
5. Confirm one row exists per Kitchen Counter used by the order.
6. Confirm `Printer Name` is `Kitchen Printer` for the current one-printer test.
7. Confirm the worker moves the queue row `Pending -> Printing -> Printed`.
8. Confirm the paper ticket contains only the items for that counter.

For a multi-counter order, separate queue rows are created. Multiple counters may intentionally point to the same physical printer during testing.

## 8. Error meanings

- `Printer not configured for Kitchen Counter ...`: set `Kitchen Counter > Printer Name` and update the already-created queue row's `printer_name` before retrying.
- `Printer '...' is not installed on this Windows PC`: make the ERPNext printer name exactly match `Get-Printer` output.
- `401 AuthenticationError`: use API credentials generated on `ourcity.s.frappe.cloud`.
- `403 PermissionError`: give the API user the required Read/Write/Create permissions.
- `wkhtmltopdf` error: verify `WKHTMLTOPDF` path.
- SumatraPDF error: verify `SUMATRA_PDF_PATH` and printer availability.
