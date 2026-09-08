"""Cashier BCN Print Job polling client for the Windows print app."""

from dataclasses import dataclass
import logging
import time

import requests

from printer_handlers import print_single_job

log = logging.getLogger("polling_client")


def build_api_url(cfg: dict, method: str) -> str:
    base = str(cfg.get("FRAPPE_BASE_URL") or "").strip().rstrip("/")
    if not base:
        raise ValueError("FRAPPE_BASE_URL is required for cashier polling")
    return f"{base}/api/method/{method}"


def auth_headers(cfg: dict) -> dict[str, str]:
    key = str(cfg.get("API_KEY") or "").strip()
    secret = str(cfg.get("API_SECRET") or "").strip()
    if not key or not secret:
        raise ValueError("API_KEY and API_SECRET are required for cashier polling")
    return {"Authorization": f"token {key}:{secret}"}


def frappe_message(response) -> dict:
    response.raise_for_status()
    payload = response.json()
    message = payload.get("message")
    if not isinstance(message, dict):
        raise ValueError("Frappe API returned an invalid message envelope")
    return message


def claim_job(session, cfg: dict, printers: list[str]) -> dict | None:
    response = session.post(
        build_api_url(cfg, "bcn_print_jobs"),
        json={"printers": printers},
        headers=auth_headers(cfg),
        timeout=30,
    )
    message = frappe_message(response)
    job = message.get("job")
    if job is not None and not isinstance(job, dict):
        raise ValueError("Frappe API returned an invalid print job")
    return job


def report_result(
    session,
    cfg: dict,
    job_name: str,
    status: str,
    error_message: str = "",
) -> dict:
    if status not in {"Printed", "Failed"}:
        raise ValueError("Print result status must be Printed or Failed")

    payload = {
        "job_name": job_name,
        "status": status,
    }
    if status == "Failed" and error_message:
        payload["error_message"] = error_message

    response = session.post(
        build_api_url(cfg, "bcn_print_job_result"),
        json=payload,
        headers=auth_headers(cfg),
        timeout=30,
    )
    return frappe_message(response)


@dataclass
class PendingResult:
    job_name: str
    status: str
    error_message: str = ""


def process_claimed_job(job: dict, cfg: dict) -> PendingResult:
    job_name = str(job.get("name") or "").strip()
    if not job_name:
        raise ValueError("Claimed print job has no name")

    try:
        print_single_job(job, cfg)
        return PendingResult(job_name, "Printed", "")
    except Exception as exc:
        log.exception("Cashier print failed for job %s", job_name)
        return PendingResult(job_name, "Failed", str(exc))


def flush_pending_result(session, cfg: dict, pending: PendingResult) -> bool:
    try:
        report_result(
            session,
            cfg,
            pending.job_name,
            pending.status,
            error_message=pending.error_message,
        )
        return True
    except requests.RequestException as exc:
        log.warning(
            "Cashier result acknowledgement failed for %s: %s",
            pending.job_name,
            exc,
        )
        return False


def _poll_interval(cfg: dict) -> float:
    try:
        value = float(cfg.get("POLL_INTERVAL_SECONDS", 2))
    except (TypeError, ValueError):
        return 2.0
    return value if value > 0 else 2.0


def run_polling_loop(cfg, get_printers, stop_requested) -> None:
    """Claim, print and acknowledge cashier jobs without duplicate printing."""
    session = requests.Session()
    pending_result = None
    interval = _poll_interval(cfg)

    while not stop_requested():
        if pending_result is not None:
            if flush_pending_result(session, cfg, pending_result):
                pending_result = None
            time.sleep(interval)
            continue

        printers = list(get_printers() or [])
        if not printers:
            log.warning("No local printers detected; cashier poll skipped.")
            time.sleep(interval)
            continue

        try:
            job = claim_job(session, cfg, printers)
        except (requests.RequestException, ValueError) as exc:
            log.warning("Cashier print claim failed: %s", exc)
            time.sleep(interval)
            continue

        if job is not None:
            pending_result = process_claimed_job(job, cfg)
            if flush_pending_result(session, cfg, pending_result):
                pending_result = None

        time.sleep(interval)
