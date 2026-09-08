# ERPNext Server Script
# Script Type: DocType Event
# Reference DocType: Sales Order
# Event: After Insert
#
# Paste only the executable body below into ERPNext Server Script.
# No imports are required; `doc` and `frappe` are provided by Frappe.

counter_items = {}
missing_counter_items = []

for row in doc.items:
    counter = row.get("custom_kitchen_counter")
    counter = str(counter or "").strip()

    if not counter or counter == "0":
        counter = frappe.db.get_value(
            "Item",
            row.item_code,
            "custom_kitchen_counter",
        )
        counter = str(counter or "").strip()

    if not counter or counter == "0":
        item_group = frappe.db.get_value(
            "Item",
            row.item_code,
            "item_group",
        )
        item_group = str(item_group or "").strip()
        if item_group and frappe.db.exists("Kitchen Counter", item_group):
            counter = item_group

    if not counter or counter == "0":
        missing_counter_items.append(row.item_code)
        continue

    if counter not in counter_items:
        counter_items[counter] = []

    counter_items[counter].append(
        {
            "item_code": row.item_code,
            "item_name": row.item_name or row.item_code,
            "qty": row.qty,
            "uom": row.uom or row.stock_uom,
            "note": row.get("custom_kitchen_note") or "",
        }
    )

if missing_counter_items:
    frappe.log_error(
        message=(
            "Sales Order "
            + doc.name
            + " has item(s) without Kitchen Counter: "
            + ", ".join(missing_counter_items)
        ),
        title="Kitchen Print Queue Routing",
    )

for counter in counter_items:
    queue_key = doc.name + "|" + counter

    if frappe.db.exists("Kitchen Print Queue", {"queue_key": queue_key}):
        continue

    printer_name = frappe.db.get_value(
        "Kitchen Counter",
        counter,
        "custom_printer_name",
    )

    status = "Pending"
    last_error = ""
    if not printer_name:
        status = "Error"
        last_error = "Printer not configured for Kitchen Counter " + counter

    queue_doc = frappe.get_doc(
        {
            "doctype": "Kitchen Print Queue",
            "sales_order": doc.name,
            "kitchen_counter": counter,
            "printer_name": printer_name or "",
            "items_json": frappe.as_json(counter_items[counter]),
            "print_format": "Kitchen Ticket",
            "status": status,
            "retry_count": 0,
            "last_error": last_error,
            "queue_key": queue_key,
        }
    )
    queue_doc.insert(ignore_permissions=True)
