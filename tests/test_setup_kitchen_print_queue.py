import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import erpnext.setup_kitchen_print_queue as setup


def test_build_kitchen_counter_doctype_payload_contains_required_fields():
    payload = setup.build_kitchen_counter_doctype_payload("Selling")
    assert payload["name"] == "Kitchen Counter"
    assert payload["module"] == "Selling"
    assert payload["autoname"] == "field:counter_name"

    fields = {field["fieldname"]: field for field in payload["fields"]}
    assert fields["counter_name"]["reqd"] == 1
    assert fields["counter_name"]["unique"] == 1
    assert fields["custom_printer_name"]["fieldtype"] == "Data"


def test_queue_doctype_payload_contains_required_fields_and_unique_key():
    payload = setup.build_queue_doctype_payload("Selling")
    assert payload["name"] == "Kitchen Print Queue"
    assert payload["module"] == "Selling"
    assert payload["custom"] == 1
    assert payload["autoname"] == "format:KPQ-{#####}"

    fields = {field["fieldname"]: field for field in payload["fields"]}
    assert fields["sales_order"]["options"] == "Sales Order"
    assert fields["kitchen_counter"]["options"] == "Kitchen Counter"
    assert fields["status"]["options"] == "Pending\nPrinting\nPrinted\nError"
    assert fields["status"]["default"] == "Pending"
    assert fields["retry_count"]["default"] == "0"
    assert fields["queue_key"]["unique"] == 1


def test_ensure_setup_creates_kitchen_counter_before_queue():
    class FakeClient:
        def __init__(self):
            self.created = []

        def exists(self, doctype, name):
            return False

        def create(self, doctype, payload):
            self.created.append((doctype, payload))
            return payload

    client = FakeClient()
    setup.ensure_setup(client, "Selling")

    assert client.created[0][0] == "DocType"
    assert client.created[0][1]["name"] == "Kitchen Counter"
    assert any(
        dt == "DocType" and payload.get("name") == "Kitchen Print Queue"
        for dt, payload in client.created
    )


def test_existing_sales_order_item_fields_are_not_recreated():
    existing = {
        ("Custom Field", "Sales Order Item-custom_kitchen_counter"),
        ("Custom Field", "Sales Order Item-custom_kitchen_note"),
    }

    class FakeClient:
        def __init__(self):
            self.created = []

        def exists(self, doctype, name):
            return (doctype, name) in existing

        def create(self, doctype, payload):
            self.created.append((doctype, payload))
            return payload

    client = FakeClient()
    setup.ensure_setup(client, "Selling")

    created_custom_fields = [
        payload["dt"] + "-" + payload["fieldname"]
        for dt, payload in client.created
        if dt == "Custom Field"
    ]
    assert "Sales Order Item-custom_kitchen_counter" not in created_custom_fields
    assert "Sales Order Item-custom_kitchen_note" not in created_custom_fields
    assert "Item-custom_kitchen_counter" in created_custom_fields
