import importlib
import unittest


JOB_ID = "11111111-1111-4111-8111-111111111111"
VALID_JOB = {
    "job_id": JOB_ID,
    "printer": "Kitchen Printer 1",
    "payload": "JVBERi0xLjQ=",
    "ticket_type": "Kitchen",
    "attempt_count": 1,
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, *, json, timeout):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.responses.pop(0)


def load_client_module():
    try:
        return importlib.import_module("print_job_client")
    except ModuleNotFoundError as exc:
        raise AssertionError("print_job_client must provide the durable REST client") from exc


def load_client_types():
    module = load_client_module()
    return module.PrintJob, module.PrintJobClient


class PrintJobClientClaimTests(unittest.TestCase):
    def test_claim_posts_exact_contract_and_deserializes_job(self):
        PrintJob, PrintJobClient = load_client_types()
        session = FakeSession(FakeResponse({"message": {"jobs": [VALID_JOB]}}))
        client = PrintJobClient(
            session=session,
            base_url="https://ourcity.s.frappe.cloud/",
            worker_id="windows-kitchen-1",
        )

        jobs = client.claim()

        self.assertIs(client.session, session)
        self.assertEqual(
            session.calls,
            [
                {
                    "url": (
                        "https://ourcity.s.frappe.cloud/api/method/"
                        "local_printers.api.print_jobs.claim_jobs"
                    ),
                    "json": {"worker_id": "windows-kitchen-1", "limit": 10},
                    "timeout": 30,
                }
            ],
        )
        self.assertEqual(
            jobs,
            [
                PrintJob(
                    job_id=JOB_ID,
                    printer="Kitchen Printer 1",
                    payload="JVBERi0xLjQ=",
                    ticket_type="Kitchen",
                    attempt_count=1,
                )
            ],
        )

    def test_claim_rejects_invalid_envelopes_and_job_fields(self):
        module = load_client_module()
        invalid_payloads = [
            None,
            {},
            {"message": {}},
            {"message": {"jobs": {}}},
            {"message": {"jobs": [None]}},
            {"message": {"jobs": [{**VALID_JOB, "job_id": "not-a-uuid"}]}},
            {"message": {"jobs": [{**VALID_JOB, "printer": ""}]}},
            {"message": {"jobs": [{**VALID_JOB, "printer": "Kitchen\nPrinter"}]}},
            {"message": {"jobs": [{**VALID_JOB, "payload": "not base64"}]}},
            {"message": {"jobs": [{**VALID_JOB, "ticket_type": "Unknown"}]}},
            {"message": {"jobs": [{**VALID_JOB, "attempt_count": True}]}},
            {"message": {"jobs": [{**VALID_JOB, "attempt_count": 0}]}},
            {"message": {"jobs": [{**VALID_JOB, "attempt_count": 4}]}},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                session = FakeSession(FakeResponse(payload))
                client = module.PrintJobClient(
                    session=session,
                    base_url="https://ourcity.s.frappe.cloud",
                    worker_id="windows-kitchen-1",
                )

                with self.assertRaises(module.PrintJobProtocolError):
                    client.claim(limit=3)

    def test_claim_rejects_invalid_limit_before_http(self):
        module = load_client_module()
        for limit in (0, -1, True, "10"):
            with self.subTest(limit=limit):
                session = FakeSession()
                client = module.PrintJobClient(
                    session=session,
                    base_url="https://ourcity.s.frappe.cloud",
                    worker_id="windows-kitchen-1",
                )

                with self.assertRaises(ValueError):
                    client.claim(limit=limit)
                self.assertEqual(session.calls, [])


class PrintJobClientAcknowledgeTests(unittest.TestCase):
    def test_acknowledge_success_posts_exact_contract_without_error(self):
        module = load_client_module()
        session = FakeSession(FakeResponse({"message": {"status": "Success"}}))
        client = module.PrintJobClient(
            session=session,
            base_url="https://ourcity.s.frappe.cloud",
            worker_id="windows-kitchen-1",
        )

        result = client.acknowledge(JOB_ID, success=True)

        self.assertIsNone(result)
        self.assertEqual(
            session.calls,
            [
                {
                    "url": (
                        "https://ourcity.s.frappe.cloud/api/method/"
                        "local_printers.api.print_jobs.acknowledge"
                    ),
                    "json": {
                        "job_id": JOB_ID,
                        "worker_id": "windows-kitchen-1",
                        "success": 1,
                    },
                    "timeout": 30,
                }
            ],
        )

    def test_acknowledge_failure_posts_bounded_printer_error(self):
        module = load_client_module()
        session = FakeSession(FakeResponse({"message": {"status": "Pending"}}))
        client = module.PrintJobClient(
            session=session,
            base_url="https://ourcity.s.frappe.cloud",
            worker_id="windows-kitchen-1",
        )

        client.acknowledge(JOB_ID, success=False, error="Paper jam")

        self.assertEqual(
            session.calls[0]["json"],
            {
                "job_id": JOB_ID,
                "worker_id": "windows-kitchen-1",
                "success": 0,
                "error": "Paper jam",
            },
        )

    def test_acknowledge_rejects_invalid_response_status(self):
        module = load_client_module()
        session = FakeSession(FakeResponse({"message": {"status": "Printing"}}))
        client = module.PrintJobClient(
            session=session,
            base_url="https://ourcity.s.frappe.cloud",
            worker_id="windows-kitchen-1",
        )

        with self.assertRaises(module.PrintJobProtocolError):
            client.acknowledge(JOB_ID, success=True)


if __name__ == "__main__":
    unittest.main()
