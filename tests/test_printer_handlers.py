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
