"""Datalab convert API. One paid call per declaration, then the markdown stays in the database."""

import time
from decimal import Decimal

import httpx

from .base import OCRError


class DatalabError(OCRError):
    pass


def usd_from_breakdown(breakdown) -> Decimal | None:
    """Datalab reports cost_breakdown in cents. Unknown shapes are left unset."""
    if not isinstance(breakdown, dict):
        return None
    for key in ("total_cents", "cost_cents", "billed_amount_cents", "amount_cents"):
        if breakdown.get(key) is not None:
            return (Decimal(str(breakdown[key])) / Decimal("100")).quantize(Decimal("0.000001"))
    total = breakdown.get("total")
    if isinstance(total, (int, float, str)) and str(total).strip():
        return (Decimal(str(total)) / Decimal("100")).quantize(Decimal("0.000001"))
    return None


class DatalabClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://www.datalab.to",
        mode: str = "accurate",
        timeout: float = 120,
        poll_interval: float = 2,
        poll_attempts: int = 180,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise DatalabError("DATALAB_API_KEY is empty.")
        self.mode = mode
        self.poll_interval = poll_interval
        self.poll_attempts = poll_attempts
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"X-API-Key": api_key},
        )

    def close(self):
        self._http.close()

    def convert_pdf(self, content: bytes, filename: str) -> dict:
        response = self._http.post(
            "/api/v1/convert",
            files={"file": (filename, content, "application/pdf")},
            data={
                "output_format": "markdown",
                "mode": self.mode,
                "paginate": "true",
                "exact_transcription": "true",
                "disable_image_extraction": "true",
                "disable_image_captions": "true",
            },
        )
        response.raise_for_status()
        body = response.json()
        if body.get("success") is False:
            raise DatalabError(body.get("error") or "Datalab rejected the document")
        return self._poll(body["request_check_url"])

    def _poll(self, check_url: str) -> dict:
        for _ in range(self.poll_attempts):
            response = self._http.get(check_url)
            response.raise_for_status()
            body = response.json()
            status = body.get("status")
            if status == "complete" or body.get("success") is False and status != "processing":
                return self._finish(body)
            if status == "failed":
                raise DatalabError(body.get("error") or "Datalab conversion failed")
            time.sleep(self.poll_interval)
        raise DatalabError("Datalab conversion timed out")

    def _finish(self, body: dict) -> dict:
        if body.get("result_url"):
            downloaded = self._http.get(body["result_url"])
            downloaded.raise_for_status()
            payload = downloaded.json()
            payload.update({key: value for key, value in body.items() if value is not None})
            body = payload
        if body.get("success") is False or body.get("status") == "failed":
            raise DatalabError(body.get("error") or "Datalab conversion failed")
        if not (body.get("markdown") or "").strip():
            raise DatalabError("Datalab returned no markdown")
        return body
