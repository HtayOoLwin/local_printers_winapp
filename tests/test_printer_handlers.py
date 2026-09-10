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


def test_print_single_job_missing_render_mode_keeps_pdf_path(monkeypatch, tmp_path):
    pdf_path = str(tmp_path / "legacy.pdf")
    calls = []

    monkeypatch.setattr(
        printer_handlers,
        "save_pdf_from_base64",
        lambda value: calls.append(("decode", value)) or pdf_path,
    )
    monkeypatch.setattr(
        printer_handlers,
        "print_pdf_silent",
        lambda pdf, printer, path, **kwargs: calls.append(
            ("print", pdf, printer, path, kwargs)
        ),
    )

    result = printer_handlers.print_single_job(
        {
            "pdf_base64": "JVBERi0xLjQKJQ==",
            "printer_name": "Cashier Printer",
        },
        {"SUMATRA_PDF_PATH": "SumatraPDF.exe"},
    )

    assert result == "Cashier Printer"
    assert calls == [
        ("decode", "JVBERi0xLjQKJQ=="),
        (
            "print",
            pdf_path,
            "Cashier Printer",
            "SumatraPDF.exe",
            {"raise_on_error": True},
        ),
    ]


def test_print_single_job_html_mode_renders_edge_then_prints(monkeypatch, tmp_path):
    rendered = tmp_path / "cashier.pdf"
    rendered.write_bytes(b"%PDF-edge")
    calls = []

    monkeypatch.setattr(
        printer_handlers,
        "render_html_to_pdf_edge",
        lambda html, edge: calls.append(("render", html, edge)) or str(rendered),
    )
    monkeypatch.setattr(
        printer_handlers,
        "print_pdf_silent",
        lambda pdf, printer, path, **kwargs: calls.append(
            ("print", pdf, printer, path, kwargs)
        ),
    )

    result = printer_handlers.print_single_job(
        {
            "render_mode": "HTML",
            "html_content": "<html><body>မြန်မာ</body></html>",
            "printer_name": "Kitchen Printer",
        },
        {
            "EDGE_PATH": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "SUMATRA_PDF_PATH": "SumatraPDF.exe",
        },
    )

    assert result == "Kitchen Printer"
    assert calls == [
        (
            "render",
            "<html><body>မြန်မာ</body></html>",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ),
        (
            "print",
            str(rendered),
            "Kitchen Printer",
            "SumatraPDF.exe",
            {"raise_on_error": True},
        ),
    ]


def test_print_single_job_html_mode_requires_html_content():
    with pytest.raises(ValueError, match="HTML print job has no html_content"):
        printer_handlers.print_single_job(
            {
                "render_mode": "HTML",
                "printer_name": "Kitchen Printer",
            },
            {
                "EDGE_PATH": "msedge.exe",
                "SUMATRA_PDF_PATH": "SumatraPDF.exe",
            },
        )


def test_print_single_job_html_mode_requires_edge_path():
    with pytest.raises(ValueError, match="EDGE_PATH is required for HTML cashier printing"):
        printer_handlers.print_single_job(
            {
                "render_mode": "HTML",
                "html_content": "<html><body>bill</body></html>",
                "printer_name": "Kitchen Printer",
            },
            {"SUMATRA_PDF_PATH": "SumatraPDF.exe"},
        )


def test_print_single_job_rejects_unknown_render_mode():
    with pytest.raises(ValueError, match="Unsupported print render_mode: RAW"):
        printer_handlers.print_single_job(
            {
                "render_mode": "RAW",
                "printer_name": "Kitchen Printer",
            },
            {},
        )
