import json
import pytest

from kitchen_queue import (
    PrinterNotInstalledError,
    build_error_update,
    is_retryable,
    parse_items_json,
    validate_printer_name,
)


def test_parse_items_json_returns_list_of_dicts():
    payload = json.dumps([{"item_code": "BBQ-001", "qty": 2}])
    assert parse_items_json(payload) == [{"item_code": "BBQ-001", "qty": 2}]


def test_parse_items_json_rejects_malformed_json():
    with pytest.raises(ValueError, match="Invalid items_json"):
        parse_items_json("[")


def test_parse_items_json_rejects_non_list_payload():
    with pytest.raises(ValueError, match="must contain a JSON list"):
        parse_items_json('{"item_code":"BBQ-001"}')


def test_retryable_pending_or_error_below_limit():
    assert is_retryable({"status": "Pending", "retry_count": 0}, 3)
    assert is_retryable({"status": "Error", "retry_count": 2}, 3)
    assert not is_retryable({"status": "Error", "retry_count": 3}, 3)
    assert not is_retryable({"status": "Printed", "retry_count": 0}, 3)


def test_validate_printer_name_requires_exact_match():
    validate_printer_name("Kitchen Printer", ["Kitchen Printer", "POS-80C"])
    with pytest.raises(PrinterNotInstalledError, match="is not installed"):
        validate_printer_name("kitchen printer", ["Kitchen Printer"])


def test_build_error_update_increments_retry_and_keeps_error_status():
    update = build_error_update({"retry_count": 1}, RuntimeError("paper jam"), 3)
    assert update == {
        "status": "Error",
        "retry_count": 2,
        "last_error": "paper jam",
    }
