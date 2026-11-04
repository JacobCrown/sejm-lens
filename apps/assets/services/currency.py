"""Converts currencies to PLN using the NBP mid rate (table A)."""

import logging
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

# Used when the NBP API is unreachable; approximate mid rates.
FALLBACK_RATES = {
    "EUR": Decimal("4.30"),
    "USD": Decimal("3.95"),
    "GBP": Decimal("5.10"),
    "CHF": Decimal("4.55"),
    "NOK": Decimal("0.37"),
    "SEK": Decimal("0.38"),
    "CZK": Decimal("0.17"),
}


def declaration_date(year: int) -> date:
    """Annual declarations describe the state on 31 December."""
    return min(date(year, 12, 31), date.today() - timedelta(days=1))


@lru_cache(maxsize=256)
def _nbp_rate(code: str, on: date) -> Decimal | None:
    start = on - timedelta(days=10)
    url = f"{settings.NBP_API_URL}/exchangerates/rates/a/{code.lower()}/{start}/{on}/?format=json"
    try:
        resp = httpx.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        rates = resp.json().get("rates", [])
        return Decimal(str(rates[-1]["mid"])) if rates else None
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("NBP unavailable for %s %s: %s", code, on, exc)
        return None


def rate_to_pln(code: str, on: date) -> tuple[Decimal | None, str]:
    """Returns (rate, source), where source is 'identity', 'nbp', 'fallback' or 'unknown'."""
    code = code.upper()
    if code == "PLN":
        return Decimal(1), "identity"
    rate = _nbp_rate(code, on)
    if rate is not None:
        return rate, "nbp"
    if code in FALLBACK_RATES:
        return FALLBACK_RATES[code], "fallback"
    return None, "unknown"
