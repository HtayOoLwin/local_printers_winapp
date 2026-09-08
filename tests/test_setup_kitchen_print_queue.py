import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from erpnext.setup_kitchen_print_queue import build_custom_field_payload, build_queue_doctype_payload


def test_build_custom_field_payload():
    payload = build_custom_field_payload()
    assert payload == {
        "dt": "Kitchen Counter",
        "label": "Printer Name",
        "fieldname": "custom_printer_name",
        "fieldtype": "Data",
    }


def test_queue_doctype_payload_contains_required_fields_and_unique_key():
    payload = build_queue_doctype_payload("Selling")
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


def test_queue_doctype_payload_grants_system_manager_access():
    payload = build_queue_doctype_payload("Selling")
    assert payload["permissions"] == [
        {
            "role": "System Manager",
            "read": 1,
            "write": 1,
            "create": 1,
            "delete": 1,
            "print": 1,
            "email": 1,
            "export": 1,
            "share": 1,
        }
    ]
