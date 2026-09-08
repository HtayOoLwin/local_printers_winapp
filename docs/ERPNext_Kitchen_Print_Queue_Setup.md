# ERPNext Kitchen Print Queue Setup (No Frappe App Required)

This setup is for `https://ourcity.s.frappe.cloud` and does **not** require installing the `local_printers` Frappe app.

## 1. Add Printer Name to Kitchen Counter

Open **Customize Form** and select **Kitchen Counter**.

Add one field:

| Property | Value |
|---|---|
| Label | Printer Name |
| Fieldname | `custom_printer_name` |
| Field Type | Data |
| Required | No |

Save and reload.

Open each Kitchen Counter and enter the exact Windows printer name, for example:

- `BBQ Counter` -> `Kitchen Printer`
- `Drink Counter` -> `POS-80C`

The value must match `Get-Printer` output exactly.

## 2. Create Custom DocType: Kitchen Print Queue

Create a **Custom DocType** named `Kitchen Print Queue`.

Recommended settings:

- Module: choose an appropriate custom module or Custom
- Is Submittable: No
- Track Changes: Yes
- Autoname: `format:KPQ-{#####}`

Add these fields in this order:

| Label | Fieldname | Type | Options / Default | Required |
|---|---|---|---|---|
| Sales Order | `sales_order` | Link | Sales Order | Yes |
| Kitchen Counter | `kitchen_counter` | Link | Kitchen Counter | Yes |
| Printer Name | `printer_name` | Data | | No |
| Items JSON | `items_json` | Long Text | | Yes |
| Print Format | `print_format` | Data | Default: `Kitchen Ticket` | No |
| Status | `status` | Select | `Pending\nPrinting\nPrinted\nError` | Yes |
| Retry Count | `retry_count` | Int | Default: `0` | Yes |
| Last Error | `last_error` | Long Text | | No |
| Printed At | `printed_at` | Datetime | | No |
| Queue Key | `queue_key` | Data | Unique = checked | Yes |

For `queue_key`, enable **Unique**.

For `status`, set default to `Pending`.

## 3. Permissions

The API user used by the Windows worker needs:

- `Kitchen Print Queue`: Read + Write
- `Sales Order`: Read

For fastest testing, use an existing ERPNext user that already has Sales Order read access, generate API Key / API Secret for that same user, and give that user's role Read + Write permission on `Kitchen Print Queue`.

Do not paste the API Secret into chat. Put it only in the Windows `config.json`.

## 4. Create Sales Order After Insert Server Script

Create a new **Server Script**:

| Setting | Value |
|---|---|
| Script Type | DocType Event |
| Reference DocType | Sales Order |
| Event | After Insert |
| Enabled | Yes |

Paste the executable body from:

`erpnext/kitchen_print_queue_server_script.py`

The script groups Sales Order Items by `custom_kitchen_counter`, creates one queue row per counter, snapshots the counter printer name, and prevents duplicates using `<sales_order>|<counter>` as the unique queue key.

## 5. Configure Windows Worker

On the Windows PC, switch the `local_printers_winapp` repository to branch:

```text
feature/kitchen-print-queue-polling
```

Copy `config copy.json` to `config.json` and set real values:

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

If SumatraPDF or wkhtmltopdf is installed in a different location, use the actual path.

## 6. Start the Worker

From PowerShell:

```powershell
cd C:\Users\htayoolwin\local_printers_winapp_code
.\.venv\Scripts\python.exe queue_worker.py
```

Or double-click:

```text
run_queue.bat
```

Expected startup output includes:

```text
Kitchen Print Queue Worker
Site: https://ourcity.s.frappe.cloud
Poll: 3s | Max retries: 3
```

A 401 means the API Key / API Secret does not belong to the `ourcity.s.frappe.cloud` user or is invalid.

## 7. First Live Test

1. Keep `queue_worker.py` running.
2. Place a new order from the mobile app.
3. Confirm the Sales Order is created.
4. Open `Kitchen Print Queue` in ERPNext.
5. Confirm one row exists per Kitchen Counter.
6. Confirm `Printer Name` matches the intended Windows printer.
7. The worker should move status `Pending -> Printing -> Printed`.
8. Confirm the paper ticket contains only items for that Kitchen Counter.

For a two-counter order, two queue rows should be created and each counter's ticket should go to its configured printer.

## 8. Error Meaning

- `Printer not configured for Kitchen Counter ...`: set `Kitchen Counter.custom_printer_name`; for the already-created queue row, also set `printer_name` manually before retrying.
- `Printer '...' is not installed on this Windows PC`: make the ERPNext printer name exactly match Windows `Get-Printer` output.
- `401 AuthenticationError`: regenerate/use API credentials from the `ourcity.s.frappe.cloud` user.
- `wkhtmltopdf` error: verify `WKHTMLTOPDF` path.
- SumatraPDF error: verify `SUMATRA_PDF_PATH` and printer availability.
