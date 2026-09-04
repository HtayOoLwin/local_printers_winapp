"""Authenticated REST client for durable Local Print Jobs."""

import base64
import binascii
import re
import uuid
from dataclasses import dataclass
from typing import Any


CLAIM_METHOD = "local_printers.api.print_jobs.claim_jobs"
ACKNOWLEDGE_METHOD = "local_printers.api.print_jobs.acknowledge"
MAX_CLAIM_LIMIT = 50
MAX_ERROR_LENGTH = 2000
VALID_TICKET_TYPES = frozenset(("Kitchen", "Cancel", "Cashier"))
WORKER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,139}\Z")


class PrintJobProtocolError(ValueError):
    """The print-job API returned data outside its documented contract."""


@dataclass(frozen=True)
class PrintJob:
    job_id: str
    printer: str
    payload: str
    ticket_type: str
    attempt_count: int


class PrintJobClient:
    """Claim durable print work with an existing authenticated session."""

    def __init__(
        self,
        *,
        session: Any,
        base_url: str,
        worker_id: str,
        timeout: int = 30,
    ) -> None:
        if not hasattr(session, "post"):
            raise TypeError("session must provide post()")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be configured")
        if not isinstance(worker_id, str) or not WORKER_ID_PATTERN.fullmatch(worker_id):
            raise ValueError("worker_id must be a stable configured identifier")
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id
        self.timeout = timeout

    def claim(self, limit: int = 10) -> list[PrintJob]:
        if type(limit) is not int or not 1 <= limit <= MAX_CLAIM_LIMIT:
            raise ValueError(f"limit must be an integer from 1 to {MAX_CLAIM_LIMIT}")

        message = self._post_method(
            CLAIM_METHOD,
            {"worker_id": self.worker_id, "limit": limit},
        )
        jobs = message.get("jobs")
        if not isinstance(jobs, list):
            raise PrintJobProtocolError("claim response message.jobs must be a list")
        return [self._deserialize_job(job) for job in jobs]

    def acknowledge(
        self,
        job_id: str,
        success: bool,
        error: str | None = None,
    ) -> None:
        canonical_job_id = self._validated_job_id(job_id)
        if type(success) is not bool:
            raise TypeError("success must be a bool")
        if error is not None and not isinstance(error, str):
            raise TypeError("error must be a string or None")
        if success and error is not None:
            raise ValueError("a successful acknowledgement cannot include an error")

        data: dict[str, Any] = {
            "job_id": canonical_job_id,
            "worker_id": self.worker_id,
            "success": 1 if success else 0,
        }
        if error is not None:
            data["error"] = error[:MAX_ERROR_LENGTH]

        message = self._post_method(ACKNOWLEDGE_METHOD, data)
        status = message.get("status")
        valid_statuses = frozenset(("Success",)) if success else frozenset(("Pending", "Failed"))
        if status not in valid_statuses:
            raise PrintJobProtocolError("acknowledge response contains an invalid status")

    def _post_method(self, method: str, data: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            f"{self.base_url}/api/method/{method}",
            json=data,
            timeout=self.timeout,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise PrintJobProtocolError("API response is not valid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
            raise PrintJobProtocolError("API response must contain a message object")
        return payload["message"]

    @classmethod
    def _deserialize_job(cls, data: Any) -> PrintJob:
        if not isinstance(data, dict):
            raise PrintJobProtocolError("each claimed job must be an object")

        job_id = cls._validated_job_id(data.get("job_id"))
        printer = data.get("printer")
        if (
            not isinstance(printer, str)
            or not printer.strip()
            or any(character in printer for character in "\r\n\0")
        ):
            raise PrintJobProtocolError("claimed job has an invalid printer")

        payload = data.get("payload")
        if not isinstance(payload, str) or not payload:
            raise PrintJobProtocolError("claimed job has an invalid payload")
        try:
            decoded_payload = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PrintJobProtocolError("claimed job has an invalid payload") from exc
        if not decoded_payload:
            raise PrintJobProtocolError("claimed job has an invalid payload")

        ticket_type = data.get("ticket_type")
        if ticket_type not in VALID_TICKET_TYPES:
            raise PrintJobProtocolError("claimed job has an invalid ticket_type")

        attempt_count = data.get("attempt_count")
        if type(attempt_count) is not int or not 1 <= attempt_count <= 3:
            raise PrintJobProtocolError("claimed job has an invalid attempt_count")

        return PrintJob(
            job_id=job_id,
            printer=printer,
            payload=payload,
            ticket_type=ticket_type,
            attempt_count=attempt_count,
        )

    @staticmethod
    def _validated_job_id(job_id: Any) -> str:
        if not isinstance(job_id, str):
            raise PrintJobProtocolError("claimed job has an invalid job_id")
        try:
            return str(uuid.UUID(job_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise PrintJobProtocolError("claimed job has an invalid job_id") from exc
