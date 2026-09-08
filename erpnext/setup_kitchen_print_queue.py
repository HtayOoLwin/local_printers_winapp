from __future__ import annotations

import json
import sys
from urllib.parse import quote

import requests


def build_custom_field_payload() -> dict:
    return {
        "dt": "Kitchen Counter",
        "label": "Printer Name",
        "fieldname": "custom_printer_name",
        "fieldtype": "Data",
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
        "permissions": [
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
        ],
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


def ensure_setup(client: SetupClient, module: str = "Selling") -> None:
    custom_field_name = "Kitchen Counter-custom_printer_name"
    if client.exists("Custom Field", custom_field_name):
        print("[OK] Kitchen Counter.custom_printer_name already exists")
    else:
        client.create("Custom Field", build_custom_field_payload())
        print("[CREATED] Kitchen Counter.custom_printer_name")

    if client.exists("DocType", "Kitchen Print Queue"):
        print("[OK] Kitchen Print Queue already exists")
    else:
        client.create("DocType", build_queue_doctype_payload(module))
        print("[CREATED] Kitchen Print Queue")


def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8") as fh:
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
