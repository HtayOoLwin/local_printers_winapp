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
    printable_width_mm: float | None = None,
) -> str:
    if not str(html or "").strip():
        raise ValueError("Cashier HTML is empty")

    edge_path = str(edge_path or "").strip()
    if not edge_path:
        raise ValueError("EDGE_PATH is required for HTML cashier printing")

    render_html = str(html)

    if printable_width_mm is not None:
        width_text = f"{float(printable_width_mm):g}"

        thermal_style = f"""
<style id="bcn-thermal-width">
@media print {{
    html,
    body {{
        width: {width_text}mm !important;
        max-width: {width_text}mm !important;
        margin: 0 !important;
        padding: 0 !important;
    }}

    .print-format {{
        width: {width_text}mm !important;
        max-width: {width_text}mm !important;
        margin: 0 !important;
    }}
}}
</style>
"""

        thermal_script = f"""
<script id="bcn-dynamic-thermal-page">
(function () {{
    function applyThermalPageSize() {{
        var target =
            document.querySelector(".print-format")
            || document.body;

        var heightPx = Math.ceil(
            Math.max(
                target.scrollHeight,
                target.getBoundingClientRect().height
            ) + 6
        );

        var oldStyle =
            document.getElementById(
                "bcn-dynamic-thermal-page-style"
            );

        if (oldStyle) {{
            oldStyle.remove();
        }}

        var pageStyle =
            document.createElement("style");

        pageStyle.id =
            "bcn-dynamic-thermal-page-style";

        pageStyle.textContent =
            "@page {{ size: {width_text}mm "
            + heightPx
            + "px; margin: 0 !important; }}";

        document.head.appendChild(pageStyle);
    }}

    applyThermalPageSize();

    if (document.fonts && document.fonts.ready) {{
        document.fonts.ready.then(
            applyThermalPageSize
        );
    }}
}})();
</script>
"""

        if "</head>" in render_html:
            render_html = render_html.replace(
                "</head>",
                thermal_style + "\n</head>",
                1,
            )
        else:
            render_html = (
                thermal_style
                + "\n"
                + render_html
            )

        if "</body>" in render_html:
            render_html = render_html.replace(
                "</body>",
                thermal_script + "\n</body>",
                1,
            )
        else:
            render_html = (
                render_html
                + "\n"
                + thermal_script
            )

    work_dir = tempfile.mkdtemp(prefix="cashier_edge_")
    html_path = os.path.join(work_dir, "bill.html")
    pdf_path = os.path.join(work_dir, "bill.pdf")
    profile_dir = os.path.join(work_dir, "profile")

    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(render_html)

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
