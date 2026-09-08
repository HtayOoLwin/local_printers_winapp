from pathlib import Path

import pytest


class FakeClient:
    def __init__(self, invoice):
        self.invoice = invoice
        self.updates = []

    def update_cashier_queue(self, name, values):
        self.updates.append((name, dict(values)))
        return values

    def get_sales_invoice(self, name):
        assert name == self.invoice["name"]
        return self.invoice


def test_cashier_queue_prints_one_draft_invoice_once(tmp_path):
    from cashier_queue_worker import process_cashier_queue_record

    pdf = tmp_path / "bill.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    rendered = []
    printed = []
    client = FakeClient(
        {
            "name": "ACC-SINV-0001",
            "docstatus": 0,
            "customer_name": "Table 08",
            "currency": "MMK",
            "grand_total": 8500,
            "items": [
                {
                    "item_code": "K00043",
                    "item_name": "S.F 12",
                    "qty": 1,
                    "rate": 8500,
                    "amount": 8500,
                }
            ],
            "taxes": [],
        }
    )

    queue = {
        "name": "CPQ-00001",
        "sales_invoice": "ACC-SINV-0001",
        "sales_order": "SAL-ORD-0001",
        "printer_name": "Cashier Printer",
        "status": "Pending",
        "retry_count": 0,
    }
    config = {
        "EDGE_PATH": "edge.exe",
        "SUMATRA_PDF_PATH": "sumatra.exe",
        "MAX_PRINT_RETRIES": 3,
        "RESTAURANT_NAME": "Doh Myot Daw BBQ & Restaurant",
    }

    def render(html, renderer_path):
        rendered.append((html, renderer_path))
        return str(pdf)

    def print_pdf(pdf_path, printer_name, sumatra_path):
        printed.append((pdf_path, printer_name, sumatra_path))

    process_cashier_queue_record(
        client,
        queue,
        config,
        ["Cashier Printer"],
        render_pdf=render,
        print_pdf=print_pdf,
    )

    assert len(rendered) == 1
    assert "ACC-SINV-0001" in rendered[0][0]
    assert "Table 08" in rendered[0][0]
    assert "S.F 12" in rendered[0][0]
    assert printed == [(str(pdf), "Cashier Printer", "sumatra.exe")]
    assert client.updates[0][1]["status"] == "Printing"
    assert client.updates[-1][1]["status"] == "Printed"


def test_cashier_queue_rejects_submitted_invoice_before_printing(tmp_path):
    from cashier_queue_worker import process_cashier_queue_record

    client = FakeClient(
        {
            "name": "ACC-SINV-0002",
            "docstatus": 1,
            "customer_name": "Table 08",
            "currency": "MMK",
            "grand_total": 8500,
            "items": [],
            "taxes": [],
        }
    )
    queue = {
        "name": "CPQ-00002",
        "sales_invoice": "ACC-SINV-0002",
        "sales_order": "SAL-ORD-0002",
        "printer_name": "Cashier Printer",
        "status": "Pending",
        "retry_count": 0,
    }
    config = {
        "EDGE_PATH": "edge.exe",
        "SUMATRA_PDF_PATH": "sumatra.exe",
        "MAX_PRINT_RETRIES": 3,
    }

    process_cashier_queue_record(
        client,
        queue,
        config,
        ["Cashier Printer"],
        render_pdf=lambda *_: pytest.fail("submitted invoice must not render"),
        print_pdf=lambda *_: pytest.fail("submitted invoice must not print"),
    )

    assert client.updates[0][1]["status"] == "Printing"
    assert client.updates[-1][1]["status"] == "Error"
    assert client.updates[-1][1]["retry_count"] == 1
    assert "Draft Sales Invoice" in client.updates[-1][1]["last_error"]


def test_frappe_client_has_cashier_queue_contract():
    from frappe_queue_client import FrappeQueueClient

    assert callable(getattr(FrappeQueueClient, "list_cashier_retryable", None))
    assert callable(getattr(FrappeQueueClient, "list_cashier_printing", None))
    assert callable(getattr(FrappeQueueClient, "update_cashier_queue", None))
    assert callable(getattr(FrappeQueueClient, "get_sales_invoice", None))
