"""
Printer handlers – receive PDF (base64-encoded) from the Frappe server
and silently print via SumatraPDF.
"""

import base64
import subprocess
import tempfile
import os
import logging
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler

import win32print

from print_job_client import PrintJob


MAX_PRINTER_ERROR_LENGTH = 1000
DEFAULT_SPOOL_TIMEOUT_SECONDS = 120
MIN_SPOOL_TIMEOUT_SECONDS = 5
MAX_SPOOL_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class PrintResult:
    success: bool
    error: str | None = None


def bounded_printer_error(error: BaseException | str) -> str:
    """Return useful single-line printer detail within the server API bound."""
    if isinstance(error, BaseException):
        detail = f"{type(error).__name__}: {error}"
    else:
        detail = str(error)
    detail = " ".join(detail.replace("\0", "").splitlines()).strip()
    return (detail or "Unknown printer error")[:MAX_PRINTER_ERROR_LENGTH]


def get_spool_timeout(config_data: dict) -> int:
    """Return a finite SumatraPDF timeout from the validated configuration."""
    timeout = config_data.get("SPOOL_TIMEOUT_SECONDS", DEFAULT_SPOOL_TIMEOUT_SECONDS)
    if (
        type(timeout) is not int
        or not MIN_SPOOL_TIMEOUT_SECONDS <= timeout <= MAX_SPOOL_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "SPOOL_TIMEOUT_SECONDS must be an integer from "
            f"{MIN_SPOOL_TIMEOUT_SECONDS} to {MAX_SPOOL_TIMEOUT_SECONDS}"
        )
    return timeout


# ---------------------------------------------------------------------------
# Logging – file + console
# ---------------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(APP_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log = logging.getLogger("printer_handlers")
log.setLevel(logging.DEBUG)

_file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "printer_handlers.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
log.addHandler(_file_handler)


def get_local_printers() -> list[str]:
    """Return names of locally-installed printers."""
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in win32print.EnumPrinters(flags)]


def print_pdf_silent(
    pdf_path: str,
    printer_name: str,
    sumatra_pdf_path: str,
    timeout_seconds: int = DEFAULT_SPOOL_TIMEOUT_SECONDS,
) -> PrintResult:
    """Print a PDF file silently using SumatraPDF."""
    command = [
        sumatra_pdf_path,
        "-print-to",
        printer_name,
        "-print-settings",
        "noscale",
        pdf_path,
    ]
    print(f"[PRINT] Sending PDF to printer '{printer_name}' ...")
    log.info("Sending temporary PDF to printer '%s' with SumatraPDF.", printer_name)
    try:
        subprocess.run(command, check=True, timeout=timeout_seconds)
        print(f"[PRINT] ✅ Sent '{pdf_path}' to printer '{printer_name}' successfully.")
        log.info("Sent %s to printer '%s'.", pdf_path, printer_name)
        return PrintResult(True)
    except subprocess.TimeoutExpired:
        error = bounded_printer_error(
            f"SumatraPDF timed out after {timeout_seconds} seconds; spool outcome is unknown "
            "and retry may duplicate the print"
        )
        print(f"[PRINT] ❌ Printing timed out for printer '{printer_name}': {error}")
        log.error("Printing timed out for '%s': %s", printer_name, error)
        return PrintResult(False, error)
    except Exception as exc:
        error = bounded_printer_error(exc)
        print(f"[PRINT] ❌ Printing failed for printer '{printer_name}': {error}")
        log.error("Printing failed for '%s': %s", printer_name, error)
        return PrintResult(False, error)


def save_pdf_from_base64(pdf_base64: str) -> str | None:
    """Decode a base64-encoded PDF and save to a temporary file. Returns the PDF path."""
    print(f"[PDF] Decoding base64 PDF ({len(pdf_base64)} chars) ...")
    fd: int | None = None
    pdf_path: str | None = None
    file_handle = None
    try:
        pdf_bytes = base64.b64decode(pdf_base64)
        fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
        file_handle = os.fdopen(fd, "wb")
        fd = None
        with file_handle as f:
            f.write(pdf_bytes)
        print(f"[PDF] ✅ Saved PDF: {pdf_path}")
        log.info("Saved PDF from base64: %s", pdf_path)
        return pdf_path
    except Exception as exc:
        print(f"[PDF] ❌ Failed to decode/save PDF: {exc}")
        log.error("Failed to decode/save PDF: %s", exc)
        if file_handle is not None:
            try:
                file_handle.close()
            except (AttributeError, OSError):
                pass
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if pdf_path is not None:
            try:
                os.remove(pdf_path)
            except OSError as cleanup_error:
                log.warning("Could not remove partial PDF %s: %s", pdf_path, cleanup_error)
        return None


def print_claimed_job(job: PrintJob, config_data: dict) -> PrintResult:
    """Print one validated durable job using its exact Windows printer name."""
    try:
        if job.printer not in get_local_printers():
            return PrintResult(
                False,
                bounded_printer_error(f"Printer '{job.printer}' is not installed"),
            )

        timeout_seconds = get_spool_timeout(config_data)
        pdf_path = save_pdf_from_base64(job.payload)
        if not pdf_path:
            return PrintResult(False, "Unable to decode the print-job PDF payload")

        sumatra_pdf_path = config_data.get(
            "SUMATRA_PDF_PATH",
            r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
        )
        try:
            return print_pdf_silent(
                pdf_path,
                job.printer,
                sumatra_pdf_path,
                timeout_seconds,
            )
        finally:
            try:
                os.remove(pdf_path)
                log.info("Removed temporary PDF: %s", pdf_path)
            except OSError as exc:
                log.warning("Could not remove temporary PDF %s: %s", pdf_path, exc)
    except Exception as exc:
        error = bounded_printer_error(exc)
        log.error("Claimed job %s could not be printed: %s", job.job_id, error)
        return PrintResult(False, error)


def print_jobs(jobs: list[dict], config_data: dict) -> list[str]:
    """
    Process a list of print jobs received from the Frappe server.

    Each job dict contains:
      - pdf_base64    : base64-encoded PDF (ready to print)
      - printer       : target printer system name
      - printer_ip    : (optional) network printer IP
      - invoice_name  : the Sales Invoice name
      - is_cashier    : whether this is the cashier copy
      - print_format  : the Print Format used server-side

    Returns a list of printer names that were printed to.
    """
    sumatra_pdf_path = config_data.get(
        "SUMATRA_PDF_PATH", r"C:\Program Files\SumatraPDF\SumatraPDF.exe"
    )
    try:
        timeout_seconds = get_spool_timeout(config_data)
    except ValueError as exc:
        log.error("Invalid spool timeout configuration: %s", exc)
        return []

    printed_to: list[str] = []

    if not isinstance(jobs, list):
        jobs = [jobs]

    print(f"\n{'='*60}")
    print(f"[JOBS] Processing {len(jobs)} print job(s)")
    print(f"{'='*60}")
    log.info("Processing %d print job(s)", len(jobs))

    for i, job in enumerate(jobs, 1):
        invoice_name = job.get("invoice_name", "unknown")
        printer_name = job.get("printer")
        print_format = job.get("print_format", "Standard")
        is_cashier = job.get("is_cashier", False)
        pdf_base64 = job.get("pdf_base64")

        print(f"\n--- Job {i}/{len(jobs)} ---")
        print(f"  Invoice   : {invoice_name}")
        print(f"  Printer   : {printer_name}")
        print(f"  Format    : {print_format}")
        print(f"  Is Cashier: {is_cashier}")
        print(f"  PDF       : {'Yes (' + str(len(pdf_base64)) + ' chars b64)' if pdf_base64 else 'NO ❌'}")

        log.info(
            "Job %d/%d – invoice=%s printer=%s format=%s is_cashier=%s pdf_b64_len=%s",
            i, len(jobs), invoice_name, printer_name, print_format, is_cashier,
            len(pdf_base64) if pdf_base64 else 0,
        )

        if not pdf_base64:
            print(f"  ⚠️  SKIPPED – no PDF content")
            log.warning("Job for invoice %s has no PDF, skipping.", invoice_name)
            continue

        if not printer_name:
            print(f"  ⚠️  SKIPPED – no printer name")
            log.warning("Job for invoice %s has no printer, skipping.", invoice_name)
            continue

        pdf_path = save_pdf_from_base64(pdf_base64)
        if pdf_path:
            try:
                result = print_pdf_silent(
                    pdf_path,
                    printer_name,
                    sumatra_pdf_path,
                    timeout_seconds,
                )
                if result.success:
                    printed_to.append(printer_name)
                else:
                    print(f"  ❌ Printing failed: {result.error}")
            finally:
                try:
                    os.remove(pdf_path)
                    log.info("Removed temporary PDF: %s", pdf_path)
                except OSError:
                    pass
        else:
            print(f"  ❌ PDF save failed – nothing sent to printer")

    print(f"\n{'='*60}")
    print(f"[JOBS] Done. Printed to: {printed_to if printed_to else 'NONE'}")
    print(f"{'='*60}\n")
    log.info("Finished processing. Printed to: %s", printed_to)

    return printed_to
