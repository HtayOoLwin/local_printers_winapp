from __future__ import annotations

import json
import sys
from urllib.parse import quote

import requests


def _system_manager_permissions() -> list[dict]:
    return [
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


def build_custom_field_payload(
    dt: str,
    label: str,
    fieldname: str,
    fieldtype: str = "Data",
    *,
    options: str | None = None,
) -> dict:
    payload = {
        "dt": dt,
        "label": label,
        "fieldname": fieldname,
        "fieldtype": fieldtype,
    }
    if options:
        payload["options"] = options
    return payload


def build_kitchen_counter_doctype_payload(module: str = "Selling") -> dict:
    return {
        "name": "Kitchen Counter",
        "module": module,
        "custom": 1,
        "is_submittable": 0,
        "track_changes": 1,
        "allow_rename": 1,
        "autoname": "field:counter_name",
        "title_field": "counter_name",
        "fields": [
            {
                "label": "Counter Name",
                "fieldname": "counter_name",
                "fieldtype": "Data",
                "reqd": 1,
                "unique": 1,
                "in_list_view": 1,
            },
            {
                "label": "Printer Name",
                "fieldname": "custom_printer_name",
                "fieldtype": "Data",
                "in_list_view": 1,
            },
        ],
        "permissions": _system_manager_permissions(),
    }


def build_queue_doctype_payload(module: str = "Selling") -> dict:
    return {
        "name": "Kitchen Print Queue",
        "module": module,
        "custom": 1,
        "is_submittable": 0,
        "track_changes": 1,
        "autoname": "format:KPQ-{#####}",
        "sort_field": "creation",
        "sort_order": "ASC",
        "fields": [
            {
                "label": "Sales Order",
                "fieldname": "sales_order",
                "fieldtype": "Link",
                "options": "Sales Order",
                "reqd": 1,
                "in_list_view": 1,
            },
            {
                "label": "Kitchen Counter",
                "fieldname": "kitchen_counter",
                "fieldtype": "Link",
                "options": "Kitchen Counter",
                "reqd": 1,
                "in_list_view": 1,
            },
            {
                "label": "Printer Name",
                "fieldname": "printer_name",
                "fieldtype": "Data",
                "in_list_view": 1,
            },
            {
                "label": "Items JSON",
                "fieldname": "items_json",
                "fieldtype": "Long Text",
                "reqd": 1,
            },
            {
                "label": "Print Format",
                "fieldname": "print_format",
                "fieldtype": "Data",
                "default": "Kitchen Ticket",
            },
            {
                "label": "Status",
                "fieldname": "status",
                "fieldtype": "Select",
                "options": "Pending\nPrinting\nPrinted\nError",
                "default": "Pending",
                "reqd": 1,
                "in_list_view": 1,
                "in_standard_filter": 1,
            },
            {
                "label": "Retry Count",
                "fieldname": "retry_count",
                "fieldtype": "Int",
                "default": "0",
                "reqd": 1,
            },
            {
                "label": "Last Error",
                "fieldname": "last_error",
                "fieldtype": "Long Text",
            },
            {
                "label": "Printed At",
                "fieldname": "printed_at",
                "fieldtype": "Datetime",
            },
            {
                "label": "Queue Key",
                "fieldname": "queue_key",
                "fieldtype": "Data",
                "reqd": 1,
                "unique": 1,
            },
        ],
        "permissions": _system_manager_permissions(),
    }


class SetupClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str, session=None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.headers = {
            "Authorization": f"token {api_key}:{api_secret}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, doctype: str, name: str | None = None) -> str:
        url = f"{self.base_url}/api/resource/{quote(doctype, safe='')}"
        if name:
            url += f"/{quote(name, safe='')}"
        return url

    def exists(self, doctype: str, name: str) -> bool:
        response = self.session.get(self._url(doctype, name), headers=self.headers, timeout=30)
        if response.status_code == 404:
            return False
        self._raise(response)
        return True

    def create(self, doctype: str, payload: dict) -> dict:
        response = self.session.post(
            self._url(doctype),
            headers=self.headers,
            json=payload,
            timeout=30,
        )
        self._raise(response)
        data = response.json()
        return data.get("data", data)

    @staticmethod
    def _raise(response) -> None:
        if response.status_code == 401:
            raise RuntimeError(
                "401 Authentication failed. Use API Key/Secret from ourcity.s.frappe.cloud."
            )
        if response.status_code == 403:
            raise RuntimeError(
                "403 Permission denied. Run this with a System Manager API user."
            )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(
                f"ERPNext returned HTTP {response.status_code}: {response.text}"
            ) from exc


def _ensure_custom_field(
    client: SetupClient,
    *,
    dt: str,
    label: str,
    fieldname: str,
    fieldtype: str = "Data",
    options: str | None = None,
) -> None:
    custom_field_name = f"{dt}-{fieldname}"
    if client.exists("Custom Field", custom_field_name):
        print(f"[OK] {dt}.{fieldname} already exists")
        return

    client.create(
        "Custom Field",
        build_custom_field_payload(
            dt,
            label,
            fieldname,
            fieldtype,
            options=options,
        ),
    )
    print(f"[CREATED] {dt}.{fieldname}")


def ensure_setup(client: SetupClient, module: str = "Selling") -> None:
    if client.exists("DocType", "Kitchen Counter"):
        print("[OK] Kitchen Counter already exists")
    else:
        client.create("DocType", build_kitchen_counter_doctype_payload(module))
        print("[CREATED] Kitchen Counter")

    _ensure_custom_field(
        client,
        dt="Item",
        label="Kitchen Counter",
        fieldname="custom_kitchen_counter",
        fieldtype="Link",
        options="Kitchen Counter",
    )
    _ensure_custom_field(
        client,
        dt="Sales Order Item",
        label="Kitchen Counter",
        fieldname="custom_kitchen_counter",
        fieldtype="Data",
    )
    _ensure_custom_field(
        client,
        dt="Sales Order Item",
        label="Kitchen Note",
        fieldname="custom_kitchen_note",
        fieldtype="Small Text",
    )

    if client.exists("DocType", "Kitchen Print Queue"):
        print("[OK] Kitchen Print Queue already exists")
    else:
        client.create("DocType", build_queue_doctype_payload(module))
        print("[CREATED] Kitchen Print Queue")


def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    cfg = load_config(config_path)
    required = ["FRAPPE_BASE_URL", "API_KEY", "API_SECRET"]
    missing = [key for key in required if not str(cfg.get(key) or "").strip()]
    if missing:
        print("Missing config values: " + ", ".join(missing))
        return 2

    client = SetupClient(
        cfg["FRAPPE_BASE_URL"],
        cfg["API_KEY"],
        cfg["API_SECRET"],
    )
    module = str(cfg.get("KITCHEN_QUEUE_MODULE") or "Selling")
    print(f"Site: {cfg['FRAPPE_BASE_URL']}")
    ensure_setup(client, module)
    print("ERPNext kitchen queue setup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
