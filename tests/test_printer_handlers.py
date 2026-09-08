import subprocess
import sys
import types

import pytest

if "win32print" not in sys.modules:
    sys.modules["win32print"] = types.SimpleNamespace(
        PRINTER_ENUM_LOCAL=2,
        EnumPrinters=lambda flags: [],
    )

import printer_handlers


def _fail(*args, **kwargs):
    raise subprocess.CalledProcessError(1, "sumatra")


def test_print_pdf_silent_keeps_legacy_non_raising_default(monkeypatch):
    monkeypatch.setattr(printer_handlers.subprocess, "run", _fail)
    printer_handlers.print_pdf_silent(
        "ticket.pdf", "Kitchen Printer", "SumatraPDF.exe"
    )


def test_print_pdf_silent_can_propagate_failure_for_queue_worker(monkeypatch):
    monkeypatch.setattr(printer_handlers.subprocess, "run", _fail)
    with pytest.raises(subprocess.CalledProcessError):
        printer_handlers.print_pdf_silent(
            "ticket.pdf",
            "Kitchen Printer",
            "SumatraPDF.exe",
            raise_on_error=True,
        )


def test_print_single_job_returns_printer_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr(
        printer_handlers,
        "save_pdf_from_base64",
        lambda value: str(tmp_path / "bill.pdf"),
    )
    calls = []
    monkeypatch.setattr(
        printer_handlers,
        "print_pdf_silent",
        lambda pdf, printer, path, **kwargs: calls.append(
            (pdf, printer, path, kwargs)
        ),
    )

    assert printer_handlers.print_single_job(
        {
            "pdf_base64": "JVBERi0xLjQKJQ==",
            "printer_name": "Cashier Printer",
            "document_name": "SAL-ORD-2026-00005",
        },
        {"SUMATRA_PDF_PATH": "SumatraPDF.exe"},
    ) == "Cashier Printer"
    assert calls == [
        (
            str(tmp_path / "bill.pdf"),
            "Cashier Printer",
            "SumatraPDF.exe",
            {"raise_on_error": True},
        )
    ]


def test_print_single_job_propagates_print_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        printer_handlers,
        "save_pdf_from_base64",
        lambda value: str(tmp_path / "bill.pdf"),
    )

    def fail_print(*args, **kwargs):
        raise RuntimeError("SumatraPDF returned exit code 1")

    monkeypatch.setattr(printer_handlers, "print_pdf_silent", fail_print)

    with pytest.raises(RuntimeError, match="SumatraPDF returned exit code 1"):
        printer_handlers.print_single_job(
            {
                "pdf_base64": "JVBERi0xLjQKJQ==",
                "printer_name": "Cashier Printer",
                "document_name": "SAL-ORD-2026-00005",
            },
            {"SUMATRA_PDF_PATH": "SumatraPDF.exe"},
        )
