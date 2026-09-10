from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def render_html_to_pdf_edge(
    html: str,
    edge_path: str,
    timeout_seconds: float = 15.0,
) -> str:
    if not str(html or "").strip():
        raise ValueError("Cashier HTML is empty")

    edge_path = str(edge_path or "").strip()
    if not edge_path:
        raise ValueError("EDGE_PATH is required for HTML cashier printing")

    work_dir = tempfile.mkdtemp(prefix="cashier_edge_")
    html_path = os.path.join(work_dir, "bill.html")
    pdf_path = os.path.join(work_dir, "bill.pdf")
    profile_dir = os.path.join(work_dir, "profile")

    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    command = [
        edge_path,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--user-data-dir={profile_dir}",
        f"--print-to-pdf={pdf_path}",
        Path(html_path).resolve().as_uri(),
    ]

    try:
        subprocess.run(command, check=True)
        deadline = time.monotonic() + float(timeout_seconds)
        while time.monotonic() < deadline:
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                return pdf_path
            time.sleep(0.05)
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    shutil.rmtree(work_dir, ignore_errors=True)
    raise RuntimeError(
        "Microsoft Edge did not create the cashier PDF before timeout"
    )
