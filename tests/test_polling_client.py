import importlib
import sys
import types
from unittest.mock import Mock

import pytest
import requests

if "win32print" not in sys.modules:
    sys.modules["win32print"] = types.SimpleNamespace(
        PRINTER_ENUM_LOCAL=2,
        PRINTER_ENUM_CONNECTIONS=4,
        EnumPrinters=lambda flags: [],
    )


def _module():
    spec = importlib.util.find_spec("polling_client")
    assert spec is not None, "polling_client.py is required"
    return importlib.import_module("polling_client")


def _cfg():
    return {
        "FRAPPE_BASE_URL": "https://ourcity.s.frappe.cloud",
        "API_KEY": "key",
        "API_SECRET": "secret",
        "POLL_INTERVAL_SECONDS": 2,
    }


def test_claim_job_posts_exact_contract():
    polling_client = _module()
    session = Mock()
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"message": {"job": None}}
    session.post.return_value = response

    assert polling_client.claim_job(session, _cfg(), ["Kitchen Printer"]) is None
    session.post.assert_called_once_with(
        "https://ourcity.s.frappe.cloud/api/method/bcn_print_jobs",
        json={"printers": ["Kitchen Printer"]},
        headers={"Authorization": "token key:secret"},
        timeout=30,
    )


def test_report_result_posts_exact_contract():
    polling_client = _module()
    session = Mock()
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"message": {"job_name": "BCN-PRINT-JOB-00001", "status": "Printed", "duplicate": False}}
    session.post.return_value = response
    result = polling_client.report_result(session, _cfg(), "BCN-PRINT-JOB-00001", "Printed")
    assert result["status"] == "Printed"


def test_report_failed_result_includes_error_message():
    polling_client = _module()
    session = Mock()
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"message": {"job_name": "BCN-PRINT-JOB-00001", "status": "Failed", "duplicate": False}}
    session.post.return_value = response
    polling_client.report_result(session, _cfg(), "BCN-PRINT-JOB-00001", "Failed", error_message="SumatraPDF returned exit code 1")
    session.post.assert_called_once_with(
        "https://ourcity.s.frappe.cloud/api/method/bcn_print_job_result",
        json={"job_name": "BCN-PRINT-JOB-00001", "status": "Failed", "error_message": "SumatraPDF returned exit code 1"},
        headers={"Authorization": "token key:secret"},
        timeout=30,
    )


def test_process_claimed_job_prints_once_and_returns_result(monkeypatch):
    polling_client = _module()
    calls = []
    monkeypatch.setattr(polling_client, "print_single_job", lambda job, cfg: calls.append(job["name"]) or job["printer_name"])
    job = {"name": "BCN-PRINT-JOB-00001", "printer_name": "Kitchen Printer", "pdf_base64": "AAA="}
    result = polling_client.process_claimed_job(job, _cfg())
    assert calls == ["BCN-PRINT-JOB-00001"]
    assert result == polling_client.PendingResult("BCN-PRINT-JOB-00001", "Printed", "")


def test_process_claimed_job_captures_exact_failure(monkeypatch):
    polling_client = _module()
    def fail_print(job, cfg):
        raise RuntimeError("SumatraPDF returned exit code 1")
    monkeypatch.setattr(polling_client, "print_single_job", fail_print)
    result = polling_client.process_claimed_job({"name": "BCN-PRINT-JOB-00001", "printer_name": "Kitchen Printer", "pdf_base64": "AAA="}, _cfg())
    assert result.status == "Failed"
    assert result.error_message == "SumatraPDF returned exit code 1"


def test_flush_pending_result_timeout_does_not_reprint(monkeypatch):
    polling_client = _module()
    session = Mock()
    calls = {"report": 0, "print": 0}
    def report(*args, **kwargs):
        calls["report"] += 1
        if calls["report"] == 1:
            raise requests.Timeout("timeout")
        return {"status": "Printed", "duplicate": True}
    monkeypatch.setattr(polling_client, "report_result", report)
    monkeypatch.setattr(polling_client, "print_single_job", lambda *args, **kwargs: calls.__setitem__("print", calls["print"] + 1))
    pending = polling_client.PendingResult("BCN-PRINT-JOB-00001", "Printed", "")
    assert polling_client.flush_pending_result(session, _cfg(), pending) is False
    assert polling_client.flush_pending_result(session, _cfg(), pending) is True
    assert calls == {"report": 2, "print": 0}


@pytest.mark.parametrize("value", [None, "", 0, -1, "bad"])
def test_poll_interval_defaults_to_two_seconds(value):
    polling_client = _module()
    cfg = _cfg()
    if value is None:
        cfg.pop("POLL_INTERVAL_SECONDS")
    else:
        cfg["POLL_INTERVAL_SECONDS"] = value
    assert polling_client._poll_interval(cfg) == 2.0


def test_loop_null_job_sleeps_default_interval(monkeypatch):
    polling_client = _module()
    cfg = _cfg(); cfg.pop("POLL_INTERVAL_SECONDS")
    calls = {"claim": 0, "sleep": []}
    monkeypatch.setattr(polling_client, "claim_job", lambda session, cfg, printers: calls.__setitem__("claim", calls["claim"] + 1))
    monkeypatch.setattr(polling_client.time, "sleep", lambda seconds: calls["sleep"].append(seconds))
    stop_checks = iter([False, True])
    polling_client.run_polling_loop(cfg, lambda: ["Kitchen Printer"], lambda: next(stop_checks))
    assert calls["claim"] == 1
    assert calls["sleep"] == [2.0]


def test_loop_blocks_new_claim_while_result_unacknowledged(monkeypatch):
    polling_client = _module()
    calls = {"claim": 0, "print": 0, "report": 0}
    def claim(session, cfg, printers):
        calls["claim"] += 1
        return {"name": "BCN-PRINT-JOB-00001", "printer_name": "Kitchen Printer", "pdf_base64": "AAA="}
    def print_once(job, cfg): calls["print"] += 1; return "Kitchen Printer"
    def report(*args, **kwargs): calls["report"] += 1; raise requests.Timeout("timeout")
    monkeypatch.setattr(polling_client, "claim_job", claim)
    monkeypatch.setattr(polling_client, "print_single_job", print_once)
    monkeypatch.setattr(polling_client, "report_result", report)
    monkeypatch.setattr(polling_client.time, "sleep", lambda seconds: None)
    stop_checks = iter([False, False, False, True])
    polling_client.run_polling_loop(_cfg(), lambda: ["Kitchen Printer"], lambda: next(stop_checks))
    assert calls["claim"] == 1
    assert calls["print"] == 1
    assert calls["report"] >= 3


def test_loop_acknowledgement_unblocks_next_claim(monkeypatch):
    polling_client = _module()
    calls = {"claim": 0, "print": 0, "report": 0}
    jobs = [{"name": "BCN-PRINT-JOB-00001", "printer_name": "Kitchen Printer", "pdf_base64": "AAA="}, None]
    def claim(session, cfg, printers): calls["claim"] += 1; return jobs.pop(0)
    def print_once(job, cfg): calls["print"] += 1; return "Kitchen Printer"
    def report(*args, **kwargs):
        calls["report"] += 1
        if calls["report"] == 1: raise requests.Timeout("timeout")
        return {"status": "Printed", "duplicate": True}
    monkeypatch.setattr(polling_client, "claim_job", claim)
    monkeypatch.setattr(polling_client, "print_single_job", print_once)
    monkeypatch.setattr(polling_client, "report_result", report)
    monkeypatch.setattr(polling_client.time, "sleep", lambda seconds: None)
    stop_checks = iter([False, False, False, True])
    polling_client.run_polling_loop(_cfg(), lambda: ["Kitchen Printer"], lambda: next(stop_checks))
    assert calls["claim"] == 2
    assert calls["print"] == 1
    assert calls["report"] == 2


def test_process_claimed_html_job_passes_snapshot_unchanged(monkeypatch):
    polling_client = _module()
    seen = []
    job = {
        "name": "BCN-PRINT-JOB-HTML-00001",
        "printer_name": "Kitchen Printer",
        "render_mode": "HTML",
        "html_content": "<html><body>မြန်မာ</body></html>",
        "pdf_base64": "",
    }

    monkeypatch.setattr(
        polling_client,
        "print_single_job",
        lambda claimed, cfg: seen.append(claimed.copy()) or claimed["printer_name"],
    )

    result = polling_client.process_claimed_job(job, _cfg())

    assert seen == [job]
    assert result == polling_client.PendingResult(
        "BCN-PRINT-JOB-HTML-00001",
        "Printed",
        "",
    )


def test_process_claimed_html_job_reports_edge_failure(monkeypatch):
    polling_client = _module()

    def fail_edge(job, cfg):
        raise RuntimeError("Microsoft Edge did not create the cashier PDF before timeout")

    monkeypatch.setattr(polling_client, "print_single_job", fail_edge)

    result = polling_client.process_claimed_job(
        {
            "name": "BCN-PRINT-JOB-HTML-00002",
            "printer_name": "Kitchen Printer",
            "render_mode": "HTML",
            "html_content": "<html><body>bill</body></html>",
            "pdf_base64": "",
        },
        _cfg(),
    )

    assert result.status == "Failed"
    assert result.error_message == (
        "Microsoft Edge did not create the cashier PDF before timeout"
    )
