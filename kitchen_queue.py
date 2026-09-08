from __future__ import annotations

import json


class PrinterNotInstalledError(RuntimeError):
    pass


def parse_items_json(items_json: str) -> list[dict]:
    try:
        value = json.loads(items_json or "")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid items_json: {exc}") from exc

    if not isinstance(value, list):
        raise ValueError("items_json must contain a JSON list")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError("items_json list must contain only objects")
    return value


def is_retryable(queue: dict, max_retries: int) -> bool:
    status = str(queue.get("status") or "")
    retry_count = int(queue.get("retry_count") or 0)
    return status in {"Pending", "Error"} and retry_count < int(max_retries)


def validate_printer_name(printer_name: str, installed_printers: list[str]) -> None:
    if not printer_name:
        raise PrinterNotInstalledError("Printer name is not configured")
    if printer_name not in installed_printers:
        raise PrinterNotInstalledError(
            f"Printer '{printer_name}' is not installed on this Windows PC"
        )


def build_error_update(queue: dict, exc: Exception, max_retries: int) -> dict:
    retry_count = int(queue.get("retry_count") or 0) + 1
    return {
        "status": "Error",
        "retry_count": min(retry_count, int(max_retries)),
        "last_error": str(exc),
    }
