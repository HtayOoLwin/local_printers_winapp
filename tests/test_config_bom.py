import json

from erpnext.setup_kitchen_print_queue import load_config as load_setup_config
from queue_worker import load_config as load_worker_config


def _write_bom_config(tmp_path):
    path = tmp_path / "config.json"
    payload = {"FRAPPE_BASE_URL": "https://ourcity.s.frappe.cloud"}
    path.write_text(json.dumps(payload), encoding="utf-8-sig")
    return path


def test_setup_config_accepts_utf8_bom(tmp_path):
    path = _write_bom_config(tmp_path)
    assert load_setup_config(str(path))["FRAPPE_BASE_URL"] == "https://ourcity.s.frappe.cloud"


def test_queue_worker_config_accepts_utf8_bom(tmp_path):
    path = _write_bom_config(tmp_path)
    assert load_worker_config(str(path))["FRAPPE_BASE_URL"] == "https://ourcity.s.frappe.cloud"
