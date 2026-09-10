"""
Printer handlers – receive PDF (base64-encoded) from the Frappe server
and silently print via SumatraPDF.
"""

import base64
import subprocess
import tempfile
import os
import logging
from logging.handlers import RotatingFileHandler

import win32print

from cashier_html import render_html_to_pdf_edge

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
    return [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL)]


def print_pdf_silent(
    pdf_path: str,
    printer_name: str,
    sumatra_pdf_path: str,
    *,
    raise_on_error: bool = False,
):
    """Print a PDF silently; optionally propagate failures to queue callers."""
    command = (
        f'"{sumatra_pdf_path}" -print-to "{printer_name}" '
        f'-print-settings "noscale" "{pdf_path}"'
    )
    print(f"[PRINT] Sending PDF to printer '{printer_name}' ...")
    log.info("SumatraPDF command: %s", command)
    try:
        subprocess.run(command, shell=True, check=True)
        print(f"[PRINT] ✅ Sent '{pdf_path}' to printer '{printer_name}' successfully.")
        log.info("Sent %s to printer '%s'.", pdf_path, printer_name)
    except subprocess.CalledProcessError as exc:
        print(f"[PRINT] ❌ SumatraPDF FAILED for printer '{printer_name}': {exc}")
        log.error("SumatraPDF failed for '%s': %s", printer_name, exc)
        if raise_on_error:
            raise
    except Exception as exc:
        print(f"[PRINT] ❌ Unexpected error printing PDF: {exc}")
        log.error("Unexpected error printing PDF: %s", exc)
        if raise_on_error:
            raise


def save_pdf_from_base64(pdf_base64: str) -> str | None:
    """Decode a base64-encoded PDF and save to a temporary file. Returns the PDF path."""
    print(f"[PDF] Decoding base64 PDF ({len(pdf_base64)} chars) ...")
    try:
        pdf_bytes = base64.b64decode(pdf_base64)
        fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as f:
            f.write(pdf_bytes)
        print(f"[PDF] ✅ Saved PDF: {pdf_path}")
        log.info("Saved PDF from base64: %s", pdf_path)
        return pdf_path
    except Exception as exc:
        print(f"[PDF] ❌ Failed to decode/save PDF: {exc}")
        log.error("Failed to decode/save PDF: %s", exc)
        return None


def print_single_job(job: dict, config_data: dict) -> str:
    """Print one queue job and raise when render/decode or physical printing fails."""
    render_mode = str(job.get("render_mode") or "PDF").strip().upper()
    printer_name = job.get("printer_name") or job.get("printer")

    if not printer_name:
        raise ValueError("Print job has no printer name")

    if render_mode == "HTML":
        html_content = str(job.get("html_content") or "")
        if not html_content.strip():
            raise ValueError("HTML print job has no html_content")

        edge_path = str(config_data.get("EDGE_PATH") or "").strip()
        if not edge_path:
            raise ValueError("EDGE_PATH is required for HTML cashier printing")

        printable_width_mm = config_data.get(
            "CASHIER_PRINTABLE_WIDTH_MM",
            72.0,
        )

        pdf_path = render_html_to_pdf_edge(
            html_content,
            edge_path,
            printable_width_mm=float(
                printable_width_mm
            ),
        )
    elif render_mode == "PDF":
        pdf_base64 = job.get("pdf_base64")
        if not pdf_base64:
            raise ValueError("Print job has no pdf_base64")

        pdf_path = save_pdf_from_base64(pdf_base64)
        if not pdf_path:
            raise ValueError("Failed to decode/save print job PDF")
    else:
        raise ValueError("Unsupported print render_mode: " + render_mode)

    sumatra_pdf_path = config_data.get(
        "SUMATRA_PDF_PATH", r"C:\Program Files\SumatraPDF\SumatraPDF.exe"
    )

    try:
        print_pdf_silent(
            pdf_path,
            printer_name,
            sumatra_pdf_path,
            raise_on_error=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"SumatraPDF returned exit code {exc.returncode}"
        ) from exc

    return printer_name


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
            i,
            len(jobs),
            invoice_name,
            printer_name,
            print_format,
            is_cashier,
            len(pdf_base64) if pdf_base64 else 0,
        )

        if not pdf_base64:
            print("  ⚠️  SKIPPED – no PDF content")
            log.warning("Job for invoice %s has no PDF, skipping.", invoice_name)
            continue

        if not printer_name:
            print("  ⚠️  SKIPPED – no printer name")
            log.warning("Job for invoice %s has no printer, skipping.", invoice_name)
            continue

        try:
            printed_to.append(print_single_job(job, config_data))
        except Exception as exc:
            print(f"  ❌ Printing failed: {exc}")
            log.error("Job for invoice %s failed: %s", invoice_name, exc)

    print(f"\n{'='*60}")
    print(f"[JOBS] Done. Printed to: {printed_to if printed_to else 'NONE'}")
    print(f"{'='*60}\n")
    log.info("Finished processing. Printed to: %s", printed_to)

    return printed_to
