import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

import cashier_html


def _path_from_file_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if os.name == "nt" and path.startswith("/") and len(path) >= 3 and path[2] == ":":
        path = path[1:]
    return Path(path)


def test_edge_renderer_writes_utf8_html_and_waits_for_nonempty_pdf(tmp_path, monkeypatch):
    edge = tmp_path / "msedge.exe"
    edge.write_text("edge", encoding="utf-8")
    seen = {}
    writers = []

    def fake_run(command, check):
        assert check is True
        html_path = _path_from_file_uri(command[-1])
        seen["html"] = html_path.read_text(encoding="utf-8")
        pdf_arg = next(x for x in command if x.startswith("--print-to-pdf="))
        pdf_path = pdf_arg.split("=", 1)[1]

        def delayed_write():
            time.sleep(0.08)
            Path(pdf_path).write_bytes(b"%PDF-cashier-edge")

        thread = threading.Thread(target=delayed_write)
        thread.start()
        writers.append(thread)

    monkeypatch.setattr(subprocess, "run", fake_run)
    html = "<html><meta charset='utf-8'><body>မြန်မာ စမ်းသပ်</body></html>"
    pdf_path = cashier_html.render_html_to_pdf_edge(html, str(edge), timeout_seconds=2)
    for thread in writers:
        thread.join()
    assert "မြန်မာ စမ်းသပ်" in seen["html"]
    assert Path(pdf_path).read_bytes() == b"%PDF-cashier-edge"


def test_edge_renderer_rejects_empty_html(tmp_path):
    with pytest.raises(ValueError, match="Cashier HTML is empty"):
        cashier_html.render_html_to_pdf_edge("   ", str(tmp_path / "msedge.exe"))


def test_edge_renderer_requires_edge_path():
    with pytest.raises(ValueError, match="EDGE_PATH is required for HTML cashier printing"):
        cashier_html.render_html_to_pdf_edge("<html><body>bill</body></html>", "")


def test_edge_renderer_raises_when_pdf_never_appears(tmp_path, monkeypatch):
    edge = tmp_path / "msedge.exe"
    edge.write_text("edge", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", lambda command, check: None)
    with pytest.raises(RuntimeError, match="Microsoft Edge did not create the cashier PDF before timeout"):
        cashier_html.render_html_to_pdf_edge("<html><body>bill</body></html>", str(edge), timeout_seconds=0.05)
