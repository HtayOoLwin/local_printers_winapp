import json
from pathlib import Path


def test_sample_config_documents_cashier_polling_contract():
    cfg = json.loads(Path("config copy.json").read_text(encoding="utf-8"))
    assert cfg["FRAPPE_BASE_URL"] == "https://ourcity.s.frappe.cloud"
    assert "API_KEY" in cfg
    assert "API_SECRET" in cfg
    assert cfg["POLL_INTERVAL_SECONDS"] == 2
    assert "SUMATRA_PDF_PATH" in cfg


def test_readme_documents_timeout_safe_cashier_polling():
    source = Path("README.md").read_text(encoding="utf-8")
    assert "BCN Print Job" in source
    assert "bcn_print_jobs" in source
    assert "bcn_print_job_result" in source
    assert "Print result unknown after client timeout" in source
    assert "does not automatically" in source
    assert "manual Reprint" in source


def test_readme_documents_dual_mode_cashier_html_edge_flow():
    source = Path("README.md").read_text(encoding="utf-8")
    assert "render_mode" in source
    assert "html_content" in source
    assert "Microsoft Edge" in source
    assert "HTML" in source
    assert "missing `render_mode`" in source
    assert "PDF" in source
    assert "wkhtmltopdf" in source
    assert "does not fall back" in source


def test_sample_config_documents_edge_path_for_html_cashier_jobs():
    cfg = json.loads(Path("config copy.json").read_text(encoding="utf-8"))
    assert cfg["EDGE_PATH"].lower().endswith("msedge.exe")
