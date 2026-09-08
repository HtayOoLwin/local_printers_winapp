"""
Local Printers Windows App - Socket.IO client.

Connects to a Frappe/ERPNext site via Socket.IO, listens for
'document_print_event' (primary) and 'sales_invoice_submitted'
(backward-compat) events, and silently prints each job to the
designated local printer.

Event contracts (mirrors utils.py):
  document_print_event  -> { doctype, document_name, method, jobs: [...] }
  sales_invoice_submitted -> [ { printer, pdf_base64, ... }, ... ]
"""

import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import urlparse

import requests
import socketio
import win32print

from polling_client import run_polling_loop
from printer_handlers import print_jobs

# ---------------------------------------------------------------------------
# Logging - file + console
# ---------------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(APP_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log = logging.getLogger("socket_app")
log.setLevel(logging.DEBUG)

if not log.handlers:
    _console_handler = logging.StreamHandler()
    _console_handler.setLevel(logging.DEBUG)
    _console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    log.addHandler(_console_handler)

    _file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "socket_app.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    log.addHandler(_file_handler)

# ---------------------------------------------------------------------------
# Socket.IO client + globals
# ---------------------------------------------------------------------------
sio = socketio.Client(reconnection=True, reconnection_delay=5)

config_data: dict[str, Any] = {}
_register_lock = Lock()
_registered_once = False
stop_event = Event()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_config(config_path: str) -> dict[str, Any]:
    """Load configuration from a JSON file."""
    print(f"[CONFIG] Loading config from '{config_path}' ...")
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        print("[CONFIG] Configuration loaded successfully.")
        log.info("Configuration loaded from %s", config_path)
        return data
    except FileNotFoundError:
        print(f"[CONFIG] Config file '{config_path}' not found.")
        sys.exit(f"Configuration file {config_path} not found.")
    except json.JSONDecodeError as exc:
        print(f"[CONFIG] Invalid JSON: {exc}")
        sys.exit(f"Invalid JSON in config file: {exc}")


def get_local_printers() -> list[str]:
    """Return names of locally-installed printers."""
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in win32print.EnumPrinters(flags)]


def build_http_base_url(cfg: dict[str, Any]) -> str:
    """
    Derive the plain HTTP base URL for API calls.
    FRAPPE_SOCKET_URL may point to a socketio port – use FRAPPE_BASE_URL
    when available, otherwise parse the host from LOGIN_URL / FRAPPE_SOCKET_URL.
    """
    base = str(cfg.get("FRAPPE_BASE_URL") or "").strip().rstrip("/")
    if base:
        return base

    for key in ("LOGIN_URL", "FRAPPE_SOCKET_URL"):
        url = str(cfg.get(key) or "").strip()
        if url:
            parsed = urlparse(url)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"

    raise ValueError("Cannot determine HTTP base URL. Set FRAPPE_BASE_URL in config.json.")


def send_printers_to_server(printers: list[str], cfg: dict[str, Any]) -> None:
    """Register local printer names on the Frappe server."""
    print(f"[SERVER] Sending {len(printers)} printer(s) to server ...")
    headers = {
        "Authorization": f"token {cfg['API_KEY']}:{cfg['API_SECRET']}",
        "Content-Type": "application/json",
    }
    try:
        base_url = build_http_base_url(cfg)
        resp = requests.post(
            f"{base_url}/api/method/local_printers.utils.save_printers_data",
            json={"printers": printers},
            headers=headers,
            timeout=30,
        )
        if resp.ok:
            print("[SERVER] Printers registered on server.")
            log.info("Printers data sent to server.")
        else:
            print(f"[SERVER] Server responded {resp.status_code}: {resp.text}")
            log.warning("Failed to send printers (%s): %s", resp.status_code, resp.text)
    except requests.RequestException as exc:
        print(f"[SERVER] Error sending printers: {exc}")
        log.error("Error sending printers to server: %s", exc)
    except Exception as exc:
        print(f"[SERVER] Configuration error: {exc}")
        log.error("Configuration error while sending printers: %s", exc)


def fetch_session_cookies(cfg: dict[str, Any]) -> str | None:
    """Log in and return a cookie header string."""
    print(f"[AUTH] Logging in to {cfg['LOGIN_URL']} ...")
    try:
        resp = requests.post(cfg["LOGIN_URL"], data=cfg["AUTH_DATA"], timeout=30)
        resp.raise_for_status()
        cookie_header = "; ".join(f"{k}={v}" for k, v in resp.cookies.items())
        if not cookie_header:
            print("[AUTH] Login response returned no cookies.")
            log.error("Login succeeded but no cookies were returned.")
            return None
        print("[AUTH] Login successful.")
        log.info("Login successful.")
        return cookie_header
    except requests.RequestException as exc:
        print(f"[AUTH] Login failed: {exc}")
        log.error("Login failed: %s", exc)
        return None


def start_cashier_polling(cfg: dict[str, Any]) -> Thread | None:
    """Start BCN Print Job polling without changing legacy Socket.IO behavior."""
    required = ("FRAPPE_BASE_URL", "API_KEY", "API_SECRET")
    missing = [key for key in required if not str(cfg.get(key) or "").strip()]
    if missing:
        print(
            "[CASHIER] Polling disabled; missing config: "
            + ", ".join(missing)
        )
        log.warning("Cashier polling disabled; missing config keys: %s", missing)
        return None

    stop_event.clear()
    thread = Thread(
        target=run_polling_loop,
        args=(cfg, get_local_printers, stop_event.is_set),
        name="cashier-print-poller",
        daemon=True,
    )
    thread.start()
    print("[CASHIER] BCN Print Job polling started.")
    log.info("BCN Print Job polling thread started.")
    return thread


def legacy_socket_configured(cfg: dict[str, Any]) -> bool:
    """Return whether the original Socket.IO/login configuration is complete."""
    required = ("LOGIN_URL", "AUTH_DATA", "FRAPPE_SOCKET_URL")
    return all(cfg.get(key) for key in required)


def extract_jobs(payload: Any) -> tuple[list[dict[str, Any]], str]:
    """
    Normalise any payload shape coming from utils.py into
    (list_of_job_dicts, invoice_name).

    Supported shapes:
      1. document_print_event  -> { doctype, document_name, method, jobs: [...] }
      2. sales_invoice_submitted -> [ { printer, pdf_base64, invoice_name, ... }, ... ]
      3. Single job dict        -> { printer, pdf_base64, ... }
    """
    if not payload:
        return [], "unknown"

    if isinstance(payload, dict):
        # Shape 1 – wrapped payload from document_print_event
        jobs = payload.get("jobs")
        if isinstance(jobs, list):
            invoice = str(payload.get("document_name") or "unknown")
            return jobs, invoice
        # Shape 3 – bare single job dict
        if "pdf_base64" in payload:
            invoice = str(payload.get("invoice_name") or payload.get("document_name") or "unknown")
            return [payload], invoice
        return [], "unknown"

    if isinstance(payload, list):
        # Shape 2 – legacy list from sales_invoice_submitted
        invoice = "unknown"
        if payload and isinstance(payload[0], dict):
            invoice = str(
                payload[0].get("invoice_name") or payload[0].get("document_name") or "unknown"
            )
        return payload, invoice

    return [], "unknown"


# ---------------------------------------------------------------------------
# Socket.IO event handlers
# ---------------------------------------------------------------------------
def on_connect() -> None:
    global _registered_once

    print("[SOCKET] Connected to server.")
    log.info("Connected to server.")

    # Avoid duplicate registration when subscribed to multiple namespaces.
    with _register_lock:
        if _registered_once:
            return
        _registered_once = True

    printers = get_local_printers()
    print(f"[SOCKET] Local printers detected: {printers}")
    log.info("Local printers: %s", printers)
    send_printers_to_server(printers, config_data)
    print("[SOCKET] Listening for: document_print_event, sales_invoice_submitted")


def on_connect_error(data: Any) -> None:
    print(f"[SOCKET] Connection error: {data}")
    log.error("Connection error: %s", data)


def on_disconnect() -> None:
    print("[SOCKET] Disconnected from server.")
    log.warning("Disconnected from server.")


def process_print_event(event_name: str, payload: Any) -> None:
    """Common handler for both print events."""
    print("\n" + ("*" * 60))
    print(f"[EVENT] Received '{event_name}' event.")
    print("*" * 60)
    log.info("Received event: %s", event_name)

    jobs, invoice = extract_jobs(payload)

    if not jobs:
        print("[EVENT] Empty or unrecognised payload – nothing to print.")
        log.warning("No printable jobs found in '%s' payload: %s", event_name, payload)
        return

    print(f"[EVENT] Invoice    : {invoice}")
    print(f"[EVENT] Total jobs : {len(jobs)}")
    for j in jobs:
        print(
            "[EVENT]   -> printer='%s'  format='%s'  cashier=%s"
            % (j.get("printer"), j.get("print_format"), j.get("is_cashier"))
        )
    log.info("Prepared %d print job(s) for invoice %s", len(jobs), invoice)

    try:
        # Pass the extracted jobs list (not raw payload) to the print handler.
        printed = print_jobs(jobs, config_data)
        print(f"[EVENT] Printing complete. Printers used: {printed if printed else 'NONE'}")
    except Exception as exc:
        print(f"[EVENT] Printing failed: {exc}")
        log.exception("Printing failed for invoice %s: %s", invoice, exc)


def handle_document_print_event(data: Any) -> None:
    """Primary event – new server contract (utils.py document_print_event)."""
    process_print_event("document_print_event", data)


def handle_sales_invoice_submitted(data: Any) -> None:
    """Backward-compat event – utils.py still fires this for Sales Invoices."""
    process_print_event("sales_invoice_submitted", data)


def register_handlers(namespace: str) -> None:
    sio.on("connect", on_connect, namespace=namespace)
    sio.on("connect_error", on_connect_error, namespace=namespace)
    sio.on("disconnect", on_disconnect, namespace=namespace)
    sio.on("document_print_event", handle_document_print_event, namespace=namespace)
    sio.on("sales_invoice_submitted", handle_sales_invoice_submitted, namespace=namespace)


# ---------------------------------------------------------------------------
# Connection logic
# ---------------------------------------------------------------------------
def run_socketio_client(cfg: dict[str, Any], namespace: str) -> None:
    """Connect to the Frappe realtime server and block until disconnected."""
    cookie_header = fetch_session_cookies(cfg)
    if not cookie_header:
        print("[SOCKET] Cannot connect – no session cookies.")
        log.error("Cannot connect without valid session cookies.")
        return

    socket_url = str(cfg["FRAPPE_SOCKET_URL"]).rstrip("/")
    socketio_path = str(cfg.get("SOCKETIO_PATH", "/socket.io")).lstrip("/")
    headers = {"Cookie": cookie_header}

    print(f"[SOCKET] Connecting to  : {socket_url}")
    print(f"[SOCKET] Namespace      : {namespace}")
    print(f"[SOCKET] Socket.IO path : /{socketio_path}")

    try:
        sio.connect(
            socket_url,
            headers=headers,
            transports=["websocket"],
            namespaces=[namespace],
            socketio_path=socketio_path,
        )
        sio.wait()
    except Exception as exc:
        print(f"[SOCKET] Connection error: {exc}")
        log.exception("Socket.IO connection error: %s", exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n" + ("=" * 60))
    print("  Local Printers App - Starting up")
    print(f"  Logs folder: {LOG_DIR}")
    print("=" * 60 + "\n")

    config_path = "config.json"
    config_data = load_config(config_path)

    NAMESPACE = str(config_data.get("FRAPPE_SOCKET_URL") or "").strip().rstrip("/")
    # TODO: use this if bench has one site on it and for older erpnext versions
    # NAMESPACE = "/"
    print(f"  Subscribed namespace: {NAMESPACE}\n")

    register_handlers(NAMESPACE)
    start_cashier_polling(config_data)

    try:
        if legacy_socket_configured(config_data):
            run_socketio_client(config_data, NAMESPACE)
        else:
            print("[SOCKET] Legacy Socket.IO disabled; cashier polling only.")
            log.info("Legacy Socket.IO disabled; cashier polling only.")
            while not stop_event.wait(1):
                pass
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        stop_event.set()
        if sio.connected:
            sio.disconnect()
