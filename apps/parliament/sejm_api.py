"""Klient oficjalnego API Sejmu RP (https://api.sejm.gov.pl)."""

import logging
import time

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class SejmAPIError(Exception):
    pass


class SejmClient:
    def __init__(
        self,
        term: int | None = None,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        retries: int = 3,
    ):
        self.term = term or settings.SEJM_TERM
        self.retries = retries
        base = (base_url or settings.SEJM_API_URL).rstrip("/")
        self._http = httpx.Client(
            base_url=f"{base}/term{self.term}",
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "SejmWatch/0.1 (+https://github.com)", "Accept": "application/json"},
        )

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get(self, path: str):
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                resp = self._http.get(path)
                if resp.status_code >= 500:
                    raise SejmAPIError(f"{resp.status_code} dla {path}")
                if resp.status_code == 404:
                    raise SejmAPIError(f"Nie znaleziono: {path}")
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, SejmAPIError) as exc:
                if isinstance(exc, SejmAPIError) and "Nie znaleziono" in str(exc):
                    raise
                last_exc = exc
                wait = 2**attempt
                logger.warning("Sejm API: %s (attempt %s), retrying in %ss", exc, attempt + 1, wait)
                time.sleep(wait)
        raise SejmAPIError(f"Failed to fetch {path}: {last_exc}")

    def term_info(self) -> dict:
        return self._get("")

    def clubs(self) -> list[dict]:
        return self._get("/clubs")

    def mps(self) -> list[dict]:
        return self._get("/MP")

    def proceedings(self) -> list[dict]:
        return self._get("/proceedings")

    def votings_index(self) -> list[dict]:
        """Voting days: [{date, proceeding, votingsNum}, ...]."""
        return self._get("/votings")

    def sitting_votings(self, sitting: int) -> list[dict]:
        return self._get(f"/votings/{sitting}")

    def voting(self, sitting: int, number: int) -> dict:
        return self._get(f"/votings/{sitting}/{number}")
