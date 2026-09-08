from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import quote

import requests


KITCHEN_GROUP_TO_COUNTER = {
    "Bar": "Bar",
    "Barbecue": "Barbecue",
    "Kitchen": "Kitchen",
}
SERVER_SCRIPT_NAME = "Kitchen Print Queue - Sales Order After Insert"


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


def build_server_script_payload(script: str) -> dict:
    return {
        "name": SERVER_SCRIPT_NAME,
        "script_type": "DocType Event",
        "reference_doctype": "Sales Order",
        "doctype_event": "After Insert",
        "disabled": 0,
        "script": script,
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

    def get(self, doctype: str, name: str) -> dict:
        response = self.session.get(self._url(doctype, name), headers=self.headers, timeout=30)
        self._raise(response)
        data = response.json()
        return data.get("data", data)

    def list_records(
        self,
        doctype: str,
        *,
        fields: list[str],
        filters: list | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        params = {
            "fields": json.dumps(fields),
            "limit_page_length": int(limit),
        }
        if filters:
            params["filters"] = json.dumps(filters)
        response = self.session.get(
            self._url(doctype),
            headers=self.headers,
            params=params,
            timeout=30,
        )
        self._raise(response)
        data = response.json()
        return data.get("data", [])

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

    def update(self, doctype: str, name: str, values: dict) -> dict:
        response = self.session.put(
            self._url(doctype, name),
            headers=self.headers,
            json=values,
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
                "403 Permission denied. The API user needs the required ERPNext role/permission."
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
    properties: dict | None = None,
) -> None:
    custom_field_name = f"{dt}-{fieldname}"
    if client.exists("Custom Field", custom_field_name):
        print(f"[OK] {dt}.{fieldname} already exists")
        return

    payload = build_custom_field_payload(
        dt,
        label,
        fieldname,
        fieldtype,
        options=options,
    )
    payload.update(properties or {})
    client.create("Custom Field", payload)
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
    _ensure_custom_field(
        client,
        dt="Sales Order",
        label="Kitchen Queue Created",
        fieldname="custom_kitchen_queue_created",
        fieldtype="Check",
        properties={"default": "0", "hidden": 1},
    )

    if client.exists("DocType", "Kitchen Print Queue"):
        print("[OK] Kitchen Print Queue already exists")
    else:
        client.create("DocType", build_queue_doctype_payload(module))
        print("[CREATED] Kitchen Print Queue")


def _normalized(value) -> str:
    return str(value or "").strip()


def ensure_kitchen_counters(client: SetupClient, default_printer: str = "") -> None:
    default_printer = _normalized(default_printer)
    for counter_name in KITCHEN_GROUP_TO_COUNTER.values():
        if client.exists("Kitchen Counter", counter_name):
            existing = client.get("Kitchen Counter", counter_name)
            current_printer = _normalized(existing.get("custom_printer_name"))
            if default_printer and current_printer in {"", "0"}:
                client.update(
                    "Kitchen Counter",
                    counter_name,
                    {"custom_printer_name": default_printer},
                )
                print(f"[UPDATED] {counter_name} printer -> {default_printer}")
            else:
                print(
                    f"[OK] Kitchen Counter {counter_name} already exists"
                    + (f" -> {current_printer}" if current_printer else "")
                )
            continue

        payload = {"counter_name": counter_name}
        if default_printer:
            payload["custom_printer_name"] = default_printer
        client.create("Kitchen Counter", payload)
        print(
            f"[CREATED] Kitchen Counter {counter_name}"
            + (f" -> {default_printer}" if default_printer else "")
        )


def sync_item_routing(client: SetupClient) -> int:
    groups = list(KITCHEN_GROUP_TO_COUNTER)
    items = client.list_records(
        "Item",
        fields=["name", "item_group", "custom_kitchen_counter"],
        filters=[["Item", "item_group", "in", groups]],
        limit=5000,
    )
    changed = 0
    for item in items:
        current = _normalized(item.get("custom_kitchen_counter"))
        if current not in {"", "0"}:
            continue
        counter = KITCHEN_GROUP_TO_COUNTER.get(item.get("item_group"))
        if not counter:
            continue
        client.update("Item", item["name"], {"custom_kitchen_counter": counter})
        changed += 1
    print(f"[ITEM ROUTING] Updated {changed} item(s).")
    return changed


def ensure_server_script(client: SetupClient, script: str) -> None:
    payload = build_server_script_payload(script)
    if client.exists("Server Script", SERVER_SCRIPT_NAME):
        values = {key: value for key, value in payload.items() if key != "name"}
        client.update("Server Script", SERVER_SCRIPT_NAME, values)
        print(f"[UPDATED] Server Script {SERVER_SCRIPT_NAME}")
        return
    client.create("Server Script", payload)
    print(f"[CREATED] Server Script {SERVER_SCRIPT_NAME}")


def load_server_script() -> str:
    path = Path(__file__).resolve().with_name("kitchen_print_queue_server_script.py")
    return path.read_text(encoding="utf-8-sig")


def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Set up ERPNext kitchen print queue.")
    parser.add_argument("config_path", nargs="?", default="config.json")
    parser.add_argument(
        "--printer",
        default=None,
        help="Initial printer for blank Kitchen Counters. Users can change it later in ERPNext.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(args.config_path)
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
    default_printer = (
        args.printer
        if args.printer is not None
        else str(cfg.get("DEFAULT_KITCHEN_PRINTER") or "")
    )

    print(f"Site: {cfg['FRAPPE_BASE_URL']}")
    ensure_setup(client, module)
    ensure_kitchen_counters(client, default_printer=default_printer)
    sync_item_routing(client)
    print("ERPNext kitchen queue setup complete.")
    print("No Server Script is required; the Windows worker discovers new Draft Sales Orders.")
    print("Printer assignments remain editable in Kitchen Counter > Printer Name.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
