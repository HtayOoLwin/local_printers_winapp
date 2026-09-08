import json
import sys
import types

if "win32print" not in sys.modules:
    sys.modules["win32print"] = types.SimpleNamespace(
        PRINTER_ENUM_LOCAL=2,
        PRINTER_ENUM_CONNECTIONS=4,
        EnumPrinters=lambda flags: [],
    )

import socket_app


def test_socket_namespace_uses_frappe_site_hostname():
    cfg = {
        "FRAPPE_SOCKET_URL": "https://ourcity.s.frappe.cloud"
    }

    assert socket_app.build_socket_namespace(cfg) == "/ourcity.s.frappe.cloud"


def test_socket_config_accepts_utf8_bom(tmp_path):
    config_path = tmp_path / "config.json"

    config_path.write_text(
        json.dumps({
            "FRAPPE_SOCKET_URL": "https://ourcity.s.frappe.cloud"
        }),
        encoding="utf-8-sig",
    )

    loaded = socket_app.load_config(str(config_path))

    assert loaded["FRAPPE_SOCKET_URL"] == "https://ourcity.s.frappe.cloud"
