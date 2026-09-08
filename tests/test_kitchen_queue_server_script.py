import json
from pathlib import Path


class Row:
    def __init__(self, item_code, item_name, row_counter="0"):
        self.item_code = item_code
        self.item_name = item_name
        self.qty = 1
        self.uom = "Nos"
        self.stock_uom = "Nos"
        self._values = {
            "custom_kitchen_counter": row_counter,
            "custom_kitchen_note": "",
        }

    def get(self, key):
        return self._values.get(key)


class Doc:
    name = "SAL-ORD-TEST-0001"
    items = [Row("BQ00011", "Black Chicken", row_counter="0")]


class QueueDoc:
    def __init__(self, payload, created):
        self.payload = payload
        self.created = created

    def insert(self, ignore_permissions=False):
        self.created.append(self.payload)


class DB:
    def get_value(self, doctype, name, fieldname):
        if doctype == "Item" and fieldname == "custom_kitchen_counter":
            return ""
        if doctype == "Item" and fieldname == "item_group":
            return "Barbecue"
        if doctype == "Kitchen Counter" and fieldname == "custom_printer_name":
            return "Kitchen Printer"
        return None

    def exists(self, doctype, filters):
        if doctype == "Kitchen Counter" and filters == "Barbecue":
            return True
        return False


class Frappe:
    def __init__(self):
        self.db = DB()
        self.created = []
        self.errors = []

    def get_doc(self, payload):
        return QueueDoc(payload, self.created)

    def as_json(self, value):
        return json.dumps(value)

    def log_error(self, **kwargs):
        self.errors.append(kwargs)


def test_zero_row_counter_falls_back_to_item_group_counter():
    root = Path(__file__).resolve().parents[1]
    script = (root / "erpnext" / "kitchen_print_queue_server_script.py").read_text(
        encoding="utf-8"
    )
    frappe = Frappe()
    exec(script, {"doc": Doc(), "frappe": frappe})

    assert len(frappe.created) == 1
    queue = frappe.created[0]
    assert queue["kitchen_counter"] == "Barbecue"
    assert queue["printer_name"] == "Kitchen Printer"
    assert queue["status"] == "Pending"
