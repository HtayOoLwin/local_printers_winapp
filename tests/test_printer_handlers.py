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


def test_print_pdf_silent_propagates_sumatra_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "sumatra")

    monkeypatch.setattr(printer_handlers.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        printer_handlers.print_pdf_silent(
            "ticket.pdf", "Kitchen Printer", "SumatraPDF.exe"
        )
