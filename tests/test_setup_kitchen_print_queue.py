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


def test_ensure_setup_creates_sales_order_queue_marker_field():
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

    marker = [
        payload
        for dt, payload in client.created
        if dt == "Custom Field"
        and payload.get("dt") == "Sales Order"
        and payload.get("fieldname") == "custom_kitchen_queue_created"
    ]
    assert len(marker) == 1
    assert marker[0]["fieldtype"] == "Check"
    assert marker[0]["default"] == "0"
    assert marker[0]["hidden"] == 1


def test_build_server_script_payload_targets_sales_order_after_insert():
    payload = setup.build_server_script_payload("print(123)")
    assert payload == {
        "name": "Kitchen Print Queue - Sales Order After Insert",
        "script_type": "DocType Event",
        "reference_doctype": "Sales Order",
        "doctype_event": "After Insert",
        "disabled": 0,
        "script": "print(123)",
    }


def test_ensure_kitchen_counters_creates_missing_and_only_fills_blank_printer():
    records = {
        ("Kitchen Counter", "Bar"): {
            "name": "Bar",
            "counter_name": "Bar",
            "custom_printer_name": "",
        },
        ("Kitchen Counter", "Barbecue"): {
            "name": "Barbecue",
            "counter_name": "Barbecue",
            "custom_printer_name": "BBQ Printer",
        },
    }

    class FakeClient:
        def __init__(self):
            self.created = []
            self.updated = []

        def exists(self, doctype, name):
            return (doctype, name) in records

        def get(self, doctype, name):
            return records[(doctype, name)]

        def create(self, doctype, payload):
            self.created.append((doctype, payload))
            return payload

        def update(self, doctype, name, values):
            self.updated.append((doctype, name, values))
            return values

    client = FakeClient()
    setup.ensure_kitchen_counters(client, default_printer="Kitchen Printer")

    assert (
        "Kitchen Counter",
        "Bar",
        {"custom_printer_name": "Kitchen Printer"},
    ) in client.updated
    assert not any(name == "Barbecue" for _, name, _ in client.updated)
    assert (
        "Kitchen Counter",
        {"counter_name": "Kitchen", "custom_printer_name": "Kitchen Printer"},
    ) in client.created


def test_sync_item_routing_updates_only_blank_or_zero_values():
    class FakeClient:
        def __init__(self):
            self.updated = []

        def list_records(self, doctype, *, fields, filters=None, limit=5000):
            assert doctype == "Item"
            return [
                {"name": "B00001", "item_group": "Bar", "custom_kitchen_counter": ""},
                {
                    "name": "BQ00011",
                    "item_group": "Barbecue",
                    "custom_kitchen_counter": "0",
                },
                {
                    "name": "K00037",
                    "item_group": "Kitchen",
                    "custom_kitchen_counter": None,
                },
                {
                    "name": "K99999",
                    "item_group": "Kitchen",
                    "custom_kitchen_counter": "Special Counter",
                },
            ]

        def update(self, doctype, name, values):
            self.updated.append((doctype, name, values))
            return values

    client = FakeClient()
    changed = setup.sync_item_routing(client)

    assert changed == 3
    assert ("Item", "B00001", {"custom_kitchen_counter": "Bar"}) in client.updated
    assert (
        "Item",
        "BQ00011",
        {"custom_kitchen_counter": "Barbecue"},
    ) in client.updated
    assert (
        "Item",
        "K00037",
        {"custom_kitchen_counter": "Kitchen"},
    ) in client.updated
    assert not any(name == "K99999" for _, name, _ in client.updated)


def test_ensure_server_script_creates_or_updates_named_script():
    class FakeClient:
        def __init__(self, exists):
            self._exists = exists
            self.created = []
            self.updated = []

        def exists(self, doctype, name):
            return self._exists

        def create(self, doctype, payload):
            self.created.append((doctype, payload))
            return payload

        def update(self, doctype, name, values):
            self.updated.append((doctype, name, values))
            return values

    fresh = FakeClient(False)
    setup.ensure_server_script(fresh, "print(1)")
    assert fresh.created[0][0] == "Server Script"
    assert fresh.created[0][1]["name"] == "Kitchen Print Queue - Sales Order After Insert"

    existing = FakeClient(True)
    setup.ensure_server_script(existing, "print(2)")
    assert existing.updated == [
        (
            "Server Script",
            "Kitchen Print Queue - Sales Order After Insert",
            {
                "script_type": "DocType Event",
                "reference_doctype": "Sales Order",
                "doctype_event": "After Insert",
                "disabled": 0,
                "script": "print(2)",
            },
        )
    ]
