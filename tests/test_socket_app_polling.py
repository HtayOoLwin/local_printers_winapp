from pathlib import Path


def test_socket_app_keeps_legacy_events_and_starts_cashier_poller():
    source = Path("socket_app.py").read_text(encoding="utf-8")

    assert "document_print_event" in source
    assert "sales_invoice_submitted" in source
    assert "run_polling_loop" in source
    assert "Thread(" in source
    assert "stop_event" in source
