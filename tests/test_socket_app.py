import importlib
import json
import subprocess
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from print_job_client import PrintJob


JOB = PrintJob(
    job_id="11111111-1111-4111-8111-111111111111",
    printer="Kitchen Printer 1",
    payload="JVBERi0xLjQ=",
    ticket_type="Kitchen",
    attempt_count=1,
)


def install_windows_stubs():
    win32print = types.ModuleType("win32print")
    win32print.PRINTER_ENUM_LOCAL = 2
    win32print.PRINTER_ENUM_CONNECTIONS = 4
    win32print.EnumPrinters = lambda flags: []
    sys.modules.setdefault("win32print", win32print)


class FakeSocketClient:
    def __init__(self):
        self.handlers = []
        self.connect_calls = []
        self.wait_calls = 0

    def on(self, event, handler, namespace=None):
        self.handlers.append((event, handler, namespace))

    def connect(self, url, **kwargs):
        self.connect_calls.append((url, kwargs))

    def wait(self):
        self.wait_calls += 1

    def disconnect(self):
        return None


def install_socket_stubs():
    install_windows_stubs()
    socketio = types.ModuleType("socketio")
    socketio.Client = lambda **kwargs: FakeSocketClient()
    sys.modules.setdefault("socketio", socketio)

    requests = types.ModuleType("requests")
    requests.RequestException = type("RequestException", (Exception,), {})
    requests.post = Mock()
    requests.Session = Mock()
    sys.modules.setdefault("requests", requests)


def load_socket_app():
    install_socket_stubs()
    module = importlib.import_module("socket_app")
    if not hasattr(module, "drain_pending_jobs"):
        raise AssertionError("socket_app must provide the guarded durable-job drain")
    return module


def load_printer_handlers():
    install_windows_stubs()
    module = importlib.import_module("printer_handlers")
    if not hasattr(module, "PrintResult") or not hasattr(module, "print_claimed_job"):
        raise AssertionError("printer_handlers must return a structured claimed-job result")
    return module


class ClaimedJobPrinterTests(unittest.TestCase):
    def test_print_claimed_job_spools_to_exact_printer_and_cleans_up(self):
        module = load_printer_handlers()
        calls = []

        with (
            patch.object(module, "get_local_printers", return_value=["Kitchen Printer 1"]),
            patch.object(module, "save_pdf_from_base64", return_value="C:/Temp/job.pdf"),
            patch.object(
                module,
                "print_pdf_silent",
                side_effect=lambda *args: calls.append(args) or module.PrintResult(True),
            ),
            patch.object(module.os, "remove") as remove,
        ):
            result = module.print_claimed_job(
                JOB,
                {
                    "SUMATRA_PDF_PATH": "C:/Program Files/SumatraPDF/SumatraPDF.exe",
                    "SPOOL_TIMEOUT_SECONDS": 45,
                },
            )

        self.assertEqual(result, module.PrintResult(success=True, error=None))
        self.assertEqual(
            calls,
            [
                (
                    "C:/Temp/job.pdf",
                    "Kitchen Printer 1",
                    "C:/Program Files/SumatraPDF/SumatraPDF.exe",
                    45,
                )
            ],
        )
        remove.assert_called_once_with("C:/Temp/job.pdf")

    def test_print_claimed_job_rejects_non_exact_printer_name(self):
        module = load_printer_handlers()
        with (
            patch.object(module, "get_local_printers", return_value=["kitchen printer 1"]),
            patch.object(module, "save_pdf_from_base64") as save_pdf,
        ):
            result = module.print_claimed_job(JOB, {})

        self.assertFalse(result.success)
        self.assertIn("not installed", result.error)
        save_pdf.assert_not_called()

    def test_print_pdf_silent_returns_bounded_failure(self):
        module = load_printer_handlers()
        failure = subprocess.CalledProcessError(1, ["SumatraPDF.exe"])
        with patch.object(module.subprocess, "run", side_effect=failure):
            result = module.print_pdf_silent(
                "C:/Temp/job.pdf",
                "Kitchen Printer 1",
                "C:/Program Files/SumatraPDF/SumatraPDF.exe",
            )

        self.assertFalse(result.success)
        self.assertIn("CalledProcessError", result.error)
        self.assertLessEqual(len(result.error), module.MAX_PRINTER_ERROR_LENGTH)

    def test_print_pdf_silent_uses_argument_list_check_and_default_timeout(self):
        module = load_printer_handlers()
        with patch.object(module.subprocess, "run") as run:
            result = module.print_pdf_silent(
                "C:/Temp/job.pdf",
                "Kitchen Printer 1",
                "C:/Program Files/SumatraPDF/SumatraPDF.exe",
            )

        self.assertTrue(result.success)
        run.assert_called_once_with(
            [
                "C:/Program Files/SumatraPDF/SumatraPDF.exe",
                "-print-to",
                "Kitchen Printer 1",
                "-print-settings",
                "noscale",
                "C:/Temp/job.pdf",
            ],
            check=True,
            timeout=120,
        )

    def test_print_pdf_timeout_returns_structured_duplicate_uncertainty(self):
        module = load_printer_handlers()
        timeout = subprocess.TimeoutExpired(["SumatraPDF.exe"], 120)
        with patch.object(module.subprocess, "run", side_effect=timeout):
            result = module.print_pdf_silent(
                "C:/Temp/job.pdf",
                "Kitchen Printer 1",
                "C:/Program Files/SumatraPDF/SumatraPDF.exe",
            )

        self.assertFalse(result.success)
        self.assertIn("outcome is unknown", result.error)
        self.assertIn("retry may duplicate", result.error)
        self.assertLessEqual(len(result.error), module.MAX_PRINTER_ERROR_LENGTH)

    def test_print_claimed_job_rejects_timeout_outside_safe_bounds(self):
        module = load_printer_handlers()
        with (
            patch.object(module, "get_local_printers", return_value=["Kitchen Printer 1"]),
            patch.object(module, "save_pdf_from_base64", return_value="C:/Temp/job.pdf"),
            patch.object(module, "print_pdf_silent") as spool,
            patch.object(module.os, "remove"),
        ):
            result = module.print_claimed_job(JOB, {"SPOOL_TIMEOUT_SECONDS": 0})

        self.assertFalse(result.success)
        self.assertIn("SPOOL_TIMEOUT_SECONDS", result.error)
        spool.assert_not_called()

    def test_print_claimed_job_cleans_up_after_spool_failure(self):
        module = load_printer_handlers()
        with (
            patch.object(module, "get_local_printers", return_value=["Kitchen Printer 1"]),
            patch.object(module, "save_pdf_from_base64", return_value="C:/Temp/job.pdf"),
            patch.object(
                module,
                "print_pdf_silent",
                return_value=module.PrintResult(False, "Paper jam"),
            ),
            patch.object(module.os, "remove") as remove,
        ):
            result = module.print_claimed_job(JOB, {})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Paper jam")
        remove.assert_called_once_with("C:/Temp/job.pdf")

    def test_partial_temp_file_is_removed_when_write_fails(self):
        module = load_printer_handlers()

        class FailingFile:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def write(self, data):
                raise OSError("disk full")

        with (
            patch.object(module.tempfile, "mkstemp", return_value=(42, "C:/Temp/partial.pdf")),
            patch.object(module.os, "fdopen", return_value=FailingFile()),
            patch.object(module.os, "remove") as remove,
        ):
            result = module.save_pdf_from_base64(JOB.payload)

        self.assertIsNone(result)
        remove.assert_called_once_with("C:/Temp/partial.pdf")


class FakePrintJobClient:
    def __init__(self, *claim_batches):
        self.claim_batches = list(claim_batches)
        self.claim_calls = []
        self.acknowledgements = []

    def claim(self, limit=10):
        self.claim_calls.append(limit)
        return self.claim_batches.pop(0) if self.claim_batches else []

    def acknowledge(self, job_id, success, error=None):
        self.acknowledgements.append((job_id, success, error))


class DurableDrainTests(unittest.TestCase):
    def setUp(self):
        self.module = load_socket_app()
        self.module._drain_lock = threading.Lock()
        self.module._drain_running = False
        self.module._drain_requested = False

    def test_success_is_acknowledged_only_after_spool_returns(self):
        client = FakePrintJobClient([JOB], [])
        events = []
        self.module.print_job_client = client

        def spool(job, config):
            events.append(("spool", job.job_id))
            return importlib.import_module("printer_handlers").PrintResult(True)

        def acknowledge(job_id, success, error=None):
            events.append(("ack", job_id, success, error))

        client.acknowledge = acknowledge
        with patch.object(self.module, "print_claimed_job", side_effect=spool):
            self.module.drain_pending_jobs()

        self.assertEqual(
            events,
            [
                ("spool", JOB.job_id),
                ("ack", JOB.job_id, True, None),
            ],
        )
        self.assertEqual(client.claim_calls, [10, 10])

    def test_failed_job_is_acknowledged_and_next_job_still_prints(self):
        second_job = PrintJob(
            job_id="22222222-2222-4222-8222-222222222222",
            printer="Kitchen Printer 2",
            payload="JVBERi0xLjQ=",
            ticket_type="Cancel",
            attempt_count=2,
        )
        client = FakePrintJobClient([JOB, second_job], [])
        self.module.print_job_client = client
        results = iter(
            [
                importlib.import_module("printer_handlers").PrintResult(False, "Paper jam"),
                importlib.import_module("printer_handlers").PrintResult(True),
            ]
        )

        with patch.object(self.module, "print_claimed_job", side_effect=lambda *args: next(results)):
            self.module.drain_pending_jobs()

        self.assertEqual(
            client.acknowledgements,
            [
                (JOB.job_id, False, "Paper jam"),
                (second_job.job_id, True, None),
            ],
        )

    def test_parallel_wake_does_not_start_a_second_drain(self):
        entered_claim = threading.Event()
        release_claim = threading.Event()
        claim_count = 0
        concurrent_claims = 0
        maximum_concurrent_claims = 0

        class BlockingClient:
            def claim(self, limit=10):
                nonlocal claim_count, concurrent_claims, maximum_concurrent_claims
                claim_count += 1
                concurrent_claims += 1
                maximum_concurrent_claims = max(
                    maximum_concurrent_claims,
                    concurrent_claims,
                )
                try:
                    entered_claim.set()
                    release_claim.wait(timeout=2)
                    return []
                finally:
                    concurrent_claims -= 1

        self.module.print_job_client = BlockingClient()
        first = threading.Thread(target=self.module.drain_pending_jobs)
        second = threading.Thread(target=self.module.drain_pending_jobs)
        first.start()
        self.assertTrue(entered_claim.wait(timeout=2))
        second.start()
        second.join(timeout=2)
        release_claim.set()
        first.join(timeout=2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(claim_count, 2)
        self.assertEqual(maximum_concurrent_claims, 1)

    def test_wake_at_final_empty_claim_runs_a_coalesced_follower_drain(self):
        claim_count = 0
        concurrent_claims = 0
        maximum_concurrent_claims = 0

        class WakeOnEmpty(list):
            def __bool__(inner_self):
                follower = threading.Thread(target=self.module.drain_pending_jobs)
                follower.start()
                follower.join(timeout=2)
                self.assertFalse(follower.is_alive())
                return False

        class BoundaryClient:
            def claim(inner_self, limit=10):
                nonlocal claim_count, concurrent_claims, maximum_concurrent_claims
                claim_count += 1
                concurrent_claims += 1
                maximum_concurrent_claims = max(
                    maximum_concurrent_claims,
                    concurrent_claims,
                )
                try:
                    if claim_count == 1:
                        return WakeOnEmpty()
                    return []
                finally:
                    concurrent_claims -= 1

        self.module.print_job_client = BoundaryClient()

        self.module.drain_pending_jobs()

        self.assertEqual(claim_count, 2)
        self.assertEqual(maximum_concurrent_claims, 1)


class SocketWakeTests(unittest.TestCase):
    def setUp(self):
        self.module = load_socket_app()
        self.module._registered_once = False

    def test_document_events_ignore_payload_and_only_wake_drain(self):
        payload = {
            "document_name": "SO-0001",
            "jobs": [{"printer": "Wrong Printer", "pdf_base64": "do-not-print-this"}],
        }
        with (
            patch.object(self.module, "drain_pending_jobs") as drain,
            patch.object(self.module, "print_jobs", create=True) as legacy_print,
        ):
            self.module.handle_document_print_event(payload)
            self.module.handle_document_print_event({"job_id": JOB.job_id})

        self.assertEqual(drain.call_count, 2)
        legacy_print.assert_not_called()

    def test_connect_and_reconnect_each_drain_but_register_printers_once(self):
        self.module.config_data = {"FRAPPE_BASE_URL": "https://ourcity.s.frappe.cloud"}
        with (
            patch.object(self.module, "get_local_printers", return_value=["Kitchen Printer 1"]),
            patch.object(self.module, "send_printers_to_server") as send_printers,
            patch.object(self.module, "drain_pending_jobs") as drain,
        ):
            self.module.on_connect()
            self.module.on_connect()

        send_printers.assert_called_once_with(["Kitchen Printer 1"], self.module.config_data)
        self.assertEqual(drain.call_count, 2)

    def test_enumeration_failure_isolated_and_registration_retries_on_reconnect(self):
        self.module.config_data = {"FRAPPE_BASE_URL": "https://ourcity.s.frappe.cloud"}
        errors = []
        with (
            patch.object(
                self.module,
                "get_local_printers",
                side_effect=[OSError("Windows spooler unavailable"), ["Kitchen Printer 1"]],
            ),
            patch.object(self.module, "send_printers_to_server", return_value=True) as send,
            patch.object(self.module, "drain_pending_jobs") as drain,
        ):
            for _ in range(2):
                try:
                    self.module.on_connect()
                except Exception as exc:
                    errors.append(exc)

        self.assertEqual(errors, [])
        send.assert_called_once_with(["Kitchen Printer 1"], self.module.config_data)
        self.assertEqual(drain.call_count, 2)
        self.assertTrue(self.module._registered_once)

    def test_failed_registration_retries_without_blocking_connect_drains(self):
        self.module.config_data = {"FRAPPE_BASE_URL": "https://ourcity.s.frappe.cloud"}
        with (
            patch.object(self.module, "get_local_printers", return_value=["Kitchen Printer 1"]),
            patch.object(
                self.module,
                "send_printers_to_server",
                side_effect=[False, True],
            ) as send,
            patch.object(self.module, "drain_pending_jobs") as drain,
        ):
            self.module.on_connect()
            self.module.on_connect()

        self.assertEqual(send.call_count, 2)
        self.assertEqual(drain.call_count, 2)
        self.assertTrue(self.module._registered_once)

    def test_cookie_session_registration_does_not_require_api_token_keys(self):
        class RegistrationResponse:
            ok = True

        class CookieSession:
            def __init__(self):
                self.calls = []

            def post(self, url, *, json, timeout):
                self.calls.append((url, json, timeout))
                return RegistrationResponse()

        session = CookieSession()
        self.module.http_session = session
        errors = []
        try:
            result = self.module.send_printers_to_server(
                ["Kitchen Printer 1"],
                {"FRAPPE_BASE_URL": "https://ourcity.s.frappe.cloud"},
            )
        except Exception as exc:
            errors.append(exc)
            result = None

        self.assertEqual(errors, [])
        self.assertTrue(result)
        self.assertEqual(
            session.calls,
            [
                (
                    "https://ourcity.s.frappe.cloud/api/method/"
                    "local_printers.utils.save_printers_data",
                    {"printers": ["Kitchen Printer 1"]},
                    30,
                )
            ],
        )


class AuthenticatedSessionTests(unittest.TestCase):
    def test_socket_login_session_is_reused_by_print_job_client(self):
        module = load_socket_app()

        class LoginResponse:
            cookies = {"sid": "dummy-session"}

            def raise_for_status(self):
                return None

        class AuthenticatedSession:
            def __init__(self):
                self.headers = {}
                self.login_calls = []

            def post(self, url, *, data, timeout):
                self.login_calls.append((url, data, timeout))
                return LoginResponse()

        session = AuthenticatedSession()
        socket_client = FakeSocketClient()
        job_client = object()
        cfg = {
            "FRAPPE_BASE_URL": "https://ourcity.s.frappe.cloud",
            "FRAPPE_SOCKET_URL": "https://ourcity.s.frappe.cloud",
            "LOGIN_URL": "https://ourcity.s.frappe.cloud/api/method/login",
            "AUTH_DATA": {"usr": "worker@example.com", "pwd": "dummy-password"},
            "API_KEY": "dummy-key",
            "API_SECRET": "dummy-secret",
            "WORKER_ID": "windows-kitchen-1",
        }

        with (
            patch.object(module.requests, "Session", return_value=session),
            patch.object(module, "PrintJobClient", return_value=job_client) as client_type,
            patch.object(module, "sio", socket_client),
        ):
            module.run_socketio_client(cfg, "/ourcity")

        client_type.assert_called_once_with(
            session=session,
            base_url="https://ourcity.s.frappe.cloud",
            worker_id="windows-kitchen-1",
        )
        self.assertIs(module.print_job_client, job_client)
        self.assertEqual(
            session.login_calls,
            [
                (
                    "https://ourcity.s.frappe.cloud/api/method/login",
                    cfg["AUTH_DATA"],
                    30,
                )
            ],
        )
        self.assertEqual(session.headers["Authorization"], "token dummy-key:dummy-secret")


class ConfigurationSampleTests(unittest.TestCase):
    def test_sample_config_is_credential_free_and_accepted_by_runtime_validators(self):
        module = load_socket_app()
        printer_handlers = load_printer_handlers()
        sample_path = Path(__file__).resolve().parents[1] / "config copy.json"
        config = json.loads(sample_path.read_text(encoding="utf-8"))

        client = importlib.import_module("print_job_client").PrintJobClient(
            session=Mock(post=Mock()),
            base_url=module.build_http_base_url(config),
            worker_id=config["WORKER_ID"],
        )

        self.assertEqual(client.base_url, "https://ourcity.s.frappe.cloud")
        self.assertEqual(client.worker_id, "ourcity-windows-printer-01")
        self.assertEqual(printer_handlers.get_spool_timeout(config), 120)
        self.assertEqual(config["API_KEY"], "")
        self.assertEqual(config["API_SECRET"], "")
        self.assertEqual(config["AUTH_DATA"]["pwd"], "replace-with-worker-password")


if __name__ == "__main__":
    unittest.main()
