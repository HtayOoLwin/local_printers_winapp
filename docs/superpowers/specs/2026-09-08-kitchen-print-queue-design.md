# Kitchen Print Queue Design

## Goal

Provide reliable kitchen printing for restaurant mobile orders without installing or deploying the `local_printers` Frappe app. Mobile orders continue to create draft Sales Orders on `ourcity.s.frappe.cloud`. ERPNext stores one durable print-queue record per Sales Order / Kitchen Counter, and a single Windows PC polls those queue records and prints each counter ticket to the Windows printer configured for that Kitchen Counter.

## Scope

This design covers first-print kitchen tickets for newly inserted draft Sales Orders only.

Included:
- Mobile order placement remains unchanged.
- Counter routing uses `Sales Order Item.custom_kitchen_counter`, with `Item.custom_kitchen_counter` as fallback.
- Users choose/change the target printer in ERPNext on each Kitchen Counter.
- One `Kitchen Print Queue` record is created per Sales Order / Kitchen Counter.
- A Windows worker polls Pending/Error queue records through standard Frappe REST APIs.
- The Windows worker renders a local kitchen-ticket PDF and prints it through the existing SumatraPDF print path.
- Print state, retry count, errors, and printed timestamp are persisted in ERPNext.
- Duplicate first prints are prevented by a unique queue key.

Not included in this phase:
- Cashier printing.
- Order-edit delta tickets.
- Cancel/void tickets.
- Manual reprint UI.
- Multiple Windows PCs claiming the same queue.
- Kitchen-monitor functionality.

## Constraints

- Do not require the `local_printers` Frappe app.
- Do not merge or depend on `local_printers` master for this solution.
- All kitchen printers are installed on one Windows PC.
- The restaurant mobile flow targets `ourcity.s.frappe.cloud`.
- The restaurant mobile branch remains `bcn-restaurant-mobile-without-kitchen-monitor`.
- Printer names stored in ERPNext must exactly match Windows printer names.
- The Windows worker uses standard Frappe REST APIs with API key/secret authentication.

## ERPNext Configuration

### Kitchen Counter custom field

Add this field to the existing `Kitchen Counter` DocType through Customize Form:

| Label | Fieldname | Type | Notes |
|---|---|---|---|
| Printer Name | `custom_printer_name` | Data | Exact Windows printer name |

Example:
- `BBQ Counter` -> `Kitchen Printer`
- `Drink Counter` -> `POS-80C`

Changing this value affects future queue records. Existing queued records keep their captured printer name for auditability and predictable retry behavior.

### Kitchen Print Queue Custom DocType

Create a custom DocType named `Kitchen Print Queue` with naming series `KPQ-.#####`.

Fields:

| Label | Fieldname | Type | Required | Notes |
|---|---|---|---|---|
| Sales Order | `sales_order` | Link / Sales Order | Yes | Source order |
| Kitchen Counter | `kitchen_counter` | Link / Kitchen Counter | Yes | Target counter |
| Printer Name | `printer_name` | Data | No | Snapshot of `Kitchen Counter.custom_printer_name` |
| Items JSON | `items_json` | Long Text | Yes | Immutable item snapshot for this counter |
| Print Format | `print_format` | Data | No | Reserved for future; default `Kitchen Ticket` |
| Status | `status` | Select | Yes | `Pending\nPrinting\nPrinted\nError` |
| Retry Count | `retry_count` | Int | Yes | Default 0 |
| Last Error | `last_error` | Long Text | No | Last Windows-worker failure |
| Printed At | `printed_at` | Datetime | No | Set after successful print |
| Queue Key | `queue_key` | Data | Yes | Unique: `<sales_order>|<kitchen_counter>` |

`queue_key` must be Unique.

Recommended permissions:
- Restaurant/operations users: Read.
- Integration user used by the Windows worker: Read + Write.
- Creation is performed by the Sales Order Server Script.

## Server Script

Create an ERPNext Server Script:
- Script Type: DocType Event
- Reference DocType: Sales Order
- Event: After Insert

Behavior:
1. Read every Sales Order Item.
2. Resolve counter from `row.custom_kitchen_counter`; if missing, read `Item.custom_kitchen_counter`.
3. Group rows by counter.
4. For each counter, create exactly one queue record.
5. Snapshot the configured `Kitchen Counter.custom_printer_name` into `printer_name`.
6. Snapshot only the kitchen-ticket data needed by Windows into `items_json`.
7. Use `queue_key = <sales_order>|<kitchen_counter>` and skip creation if the same key already exists.
8. Set `Pending` when a printer is configured.
9. Set `Error` with `Last Error = Printer not configured for Kitchen Counter <name>` when the counter has no printer.
10. If an item has no resolvable counter, do not silently discard it: create an Error Log or other visible diagnostic containing Sales Order and Item Code.

The item snapshot contains:
```json
[
  {
    "item_code": "BBQ-001",
    "item_name": "Grilled Pork",
    "qty": 2,
    "uom": "Plate",
    "note": "No spicy"
  }
]
```

The Server Script does not call Socket.IO, does not generate PDFs, and does not contact the Windows PC.

## Windows Worker

The Windows implementation lives in `local_printers_winapp` on branch `feature/kitchen-print-queue-polling`.

### Polling

The worker polls every 3 seconds by default.

It queries standard REST resources for queue records with status `Pending` or retryable `Error`, ordered oldest first. It processes one queue record at a time so one Windows PC cannot overlap local print jobs.

### Claim and state transitions

For each queue record:

```text
Pending/Error -> Printing -> Printed
                       \-> Error
```

Before rendering, the worker updates the record to `Printing`.

On success:
- `status = Printed`
- `printed_at = current timestamp`
- `last_error = empty`

On failure:
- `retry_count += 1`
- if retry count is below 3, set `status = Error` and retry on a later poll
- after 3 failed attempts, leave `status = Error`; automatic retries stop
- persist the actual exception/error text in `last_error`

The first version targets one Windows worker only; therefore it does not attempt distributed locking across multiple PCs.

### Printer validation

Before printing, compare `printer_name` with locally installed Windows printer names.

If no exact match exists:
- do not print to a fallback printer
- mark the queue record Error
- persist `Printer '<name>' is not installed on this Windows PC`

### Ticket rendering

The Windows app renders a local kitchen ticket from queue data; ERPNext does not render the PDF.

Initial ticket content:
- Restaurant heading (configurable text)
- Kitchen Counter
- Sales Order number
- Customer/table
- Order timestamp
- Item quantity
- Item name
- UOM
- Kitchen note when present

No prices, taxes, totals, or cashier information are printed on the kitchen ticket.

Rendering path:
1. Build HTML from queue record + item snapshot.
2. Convert HTML to PDF with the configured `WKHTMLTOPDF` executable.
3. Send the PDF to the configured Windows printer with the existing SumatraPDF silent print command.

This reuses the established physical-print path instead of replacing it.

## Windows Configuration

`config.json` is simplified for the queue worker:

```json
{
  "FRAPPE_BASE_URL": "https://ourcity.s.frappe.cloud",
  "API_KEY": "...",
  "API_SECRET": "...",
  "WKHTMLTOPDF": "C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe",
  "SUMATRA_PDF_PATH": "C:\\Program Files\\SumatraPDF\\SumatraPDF.exe",
  "POLL_INTERVAL_SECONDS": 3,
  "MAX_PRINT_RETRIES": 3,
  "RESTAURANT_NAME": "Doh Myot Daw BBQ & Restaurant"
}
```

The queue worker does not require Socket.IO, `LOGIN_URL`, `AUTH_DATA`, or the custom endpoint `local_printers.utils.save_printers_data`.

## API Usage

Use only standard Frappe REST resource endpoints:
- Read: `GET /api/resource/Kitchen Print Queue`
- Read one: `GET /api/resource/Kitchen Print Queue/<name>`
- Update: `PUT /api/resource/Kitchen Print Queue/<name>`

Authentication header:

```text
Authorization: token <API_KEY>:<API_SECRET>
```

The integration user must have permission to read and update `Kitchen Print Queue` and read linked Sales Order / Kitchen Counter data needed by the worker.

## Failure Handling

- ERPNext unavailable: log connection failure locally and retry on the next poll; do not mark jobs failed when they could not be fetched.
- API authentication 401: log a high-visibility authentication error and do not attempt printing.
- Queue printer missing on Windows: queue becomes Error.
- Malformed `items_json`: queue becomes Error with parsing details.
- wkhtmltopdf failure: queue becomes Error and retry count increments.
- SumatraPDF failure: queue becomes Error and retry count increments.
- Windows app restarts while a job is `Printing`: on startup, jobs left in `Printing` beyond a configurable stale threshold are reset to `Error` for retry. Initial stale threshold: 2 minutes.

## Duplicate-Print Safety

Two controls prevent normal duplicate first prints:
1. ERPNext `queue_key` is unique per Sales Order / Kitchen Counter.
2. The Windows worker never automatically processes `Printed` records.

A failed SumatraPDF command is not proof that paper did not physically print. Automatic retries therefore carry an unavoidable small duplicate-print risk when the OS reports failure after the printer accepted the job. The retry count and queue history make this visible.

## Testing Strategy

### Server Script / ERPNext setup

Manual acceptance tests on `ourcity.s.frappe.cloud`:
1. One order with items for one counter -> one Pending queue record.
2. One order with items for two counters -> two queue records.
3. Same server script execution attempted twice -> no duplicate queue record.
4. Counter missing printer -> Error queue record.
5. Item missing counter -> visible diagnostic.

### Windows unit tests

Add tests for:
- parsing queue records and `items_json`
- retry eligibility (`retry_count < 3`)
- stale `Printing` recovery
- exact printer-name validation
- REST filtering/query construction
- queue status transitions
- HTML ticket rendering escaping item names/notes

### Windows integration tests

With a fake HTTP server or mocked request boundary:
- Pending queue -> claim -> render -> successful print handler -> Printed update
- missing printer -> Error update
- renderer/print exception -> retry count increment + Error update
- API 401 -> no print attempt

### Live acceptance test

With `socket_app.py`/queue worker running on the restaurant Windows PC:
1. Place a new mobile order against `ourcity.s.frappe.cloud`.
2. Confirm draft Sales Order is created.
3. Confirm one queue record per Kitchen Counter.
4. Confirm each queue targets the ERPNext-configured printer.
5. Confirm each printer outputs only that counter's items.
6. Confirm each successful queue becomes Printed with `printed_at` populated.

## Rollout

1. Create `Kitchen Counter.custom_printer_name`.
2. Create `Kitchen Print Queue` Custom DocType and permissions.
3. Configure printer names for each Kitchen Counter.
4. Create and test the Sales Order After Insert Server Script.
5. Install/configure the updated Windows worker.
6. Run Windows automated tests.
7. Run one live order with one counter.
8. Run one live order with at least two counters.
9. Review queue history for Printed/Error correctness.

## Success Criteria

The feature is accepted when a mobile order on `ourcity.s.frappe.cloud` creates durable per-counter queue records and the single Windows PC prints each counter ticket to the printer selected in ERPNext, without requiring the `local_printers` Frappe app or Socket.IO print events.