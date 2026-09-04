"""
Local Printers Windows App - Socket.IO client.

Connects to a Frappe/ERPNext site via Socket.IO. Events only wake a
guarded REST drain; printable content is always claimed from the durable
Local Print Job API before it is sent to an exact Windows printer.
"""

import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from threading import Lock
from typing import Any
from urllib.parse import urlparse

import requests
import socketio
import win32print

from print_job_client import PrintJobClient
from printer_handlers import bounded_printer_error, print_claimed_job

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
http_session: Any | None = None
print_job_client: PrintJobClient | None = None
_register_lock = Lock()
_registered_once = False
_drain_lock = Lock()


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
        session = http_session or requests
        resp = session.post(
            f"{base_url}/api/method/local_printers.utils.save_printers_data",
            json={"printers": printers},
            **({} if http_session else {"headers": headers}),
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


def authenticate_http_session(cfg: dict[str, Any]) -> tuple[Any, str] | None:
    """Log in once and return the reusable HTTP session and Socket.IO cookie."""
    print(f"[AUTH] Logging in to {cfg['LOGIN_URL']} ...")
    try:
        session = requests.Session()
        api_key = cfg.get("API_KEY")
        api_secret = cfg.get("API_SECRET")
        if bool(api_key) != bool(api_secret):
            raise ValueError("API_KEY and API_SECRET must be configured together")
        if api_key and api_secret:
            session.headers["Authorization"] = f"token {api_key}:{api_secret}"

        resp = session.post(cfg["LOGIN_URL"], data=cfg["AUTH_DATA"], timeout=30)
        resp.raise_for_status()
        cookie_header = "; ".join(f"{k}={v}" for k, v in resp.cookies.items())
        if not cookie_header:
            print("[AUTH] Login response returned no cookies.")
            log.error("Login succeeded but no cookies were returned.")
            return None
        print("[AUTH] Login successful.")
        log.info("Login successful.")
        return session, cookie_header
    except requests.RequestException as exc:
        print(f"[AUTH] Login failed: {exc}")
        log.error("Login failed: %s", exc)
        return None
    except (KeyError, TypeError, ValueError) as exc:
        error = bounded_printer_error(exc)
        print(f"[AUTH] Configuration error: {error}")
        log.error("Authentication configuration error: %s", error)
        return None


def drain_pending_jobs() -> None:
    """Claim, print, and acknowledge durable jobs with single-drain serialization."""
    if not _drain_lock.acquire(blocking=False):
        log.debug("A print-job drain is already running; duplicate wake ignored.")
        return

    try:
        client = print_job_client
        if client is None:
            log.warning("Print-job drain requested before the REST client was configured.")
            return

        while True:
            try:
                jobs = client.claim(limit=10)
            except Exception as exc:
                error = bounded_printer_error(exc)
                log.error("Unable to claim print jobs: %s", error)
                return

            if not jobs:
                return

            for job in jobs:
                try:
                    result = print_claimed_job(job, config_data)
                except Exception as exc:
                    result_error = bounded_printer_error(exc)
                    success = False
                else:
                    success = result.success
                    result_error = result.error

                if not success and not result_error:
                    result_error = "Printer execution failed without error detail"
                if result_error:
                    result_error = bounded_printer_error(result_error)

                try:
                    client.acknowledge(
                        job.job_id,
                        success=success,
                        error=None if success else result_error,
                    )
                except Exception as exc:
                    error = bounded_printer_error(exc)
                    log.error("Could not acknowledge print job %s: %s", job.job_id, error)
                    continue

                if success:
                    log.info(
                        "Print job %s succeeded on printer '%s'.",
                        job.job_id,
                        job.printer,
                    )
                else:
                    log.warning(
                        "Print job %s failed on printer '%s': %s",
                        job.job_id,
                        job.printer,
                        result_error,
                    )
    finally:
        _drain_lock.release()


# ---------------------------------------------------------------------------
# Socket.IO event handlers
# ---------------------------------------------------------------------------
def on_connect() -> None:
    global _registered_once

    print("[SOCKET] Connected to server.")
    log.info("Connected to server.")

    should_register = False
    # Avoid duplicate registration when subscribed to multiple namespaces while
    # still draining on every connect/reconnect.
    with _register_lock:
        if not _registered_once:
            _registered_once = True
            should_register = True

    if should_register:
        printers = get_local_printers()
        print(f"[SOCKET] Local printers detected: {printers}")
        log.info("Local printers: %s", printers)
        send_printers_to_server(printers, config_data)
        print("[SOCKET] Listening for durable print-job wake events")

    drain_pending_jobs()


def on_connect_error(data: Any) -> None:
    print(f"[SOCKET] Connection error: {data}")
    log.error("Connection error: %s", data)


def on_disconnect() -> None:
    print("[SOCKET] Disconnected from server.")
    log.warning("Disconnected from server.")


def wake_print_job_drain(event_name: str) -> None:
    """Treat Socket.IO notification content as metadata and wake REST claiming."""
    print(f"[EVENT] Received '{event_name}' wake event.")
    log.info("Received print-job wake event: %s", event_name)
    drain_pending_jobs()


def handle_document_print_event(data: Any) -> None:
    """Wake the durable drain; never execute content from the event payload."""
    del data
    wake_print_job_drain("document_print_event")


def handle_sales_invoice_submitted(data: Any) -> None:
    """Backward-compatible wake event; its legacy payload is ignored."""
    del data
    wake_print_job_drain("sales_invoice_submitted")


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
    global http_session, print_job_client

    authenticated = authenticate_http_session(cfg)
    if not authenticated:
        print("[SOCKET] Cannot connect – no session cookies.")
        log.error("Cannot connect without valid session cookies.")
        return
    http_session, cookie_header = authenticated

    try:
        print_job_client = PrintJobClient(
            session=http_session,
            base_url=build_http_base_url(cfg),
            worker_id=cfg["WORKER_ID"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        error = bounded_printer_error(exc)
        print(f"[SOCKET] Print worker configuration error: {error}")
        log.error("Print worker configuration error: %s", error)
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

    try:
        run_socketio_client(config_data, NAMESPACE)
    except KeyboardInterrupt:
        log.info("Shutting down...")
        sio.disconnect()
