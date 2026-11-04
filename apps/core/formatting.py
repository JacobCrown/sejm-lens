from decimal import Decimal

CURRENCY_SYMBOLS = {"PLN": "zł", "EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF"}


def _num(value: float, decimals: int) -> str:
    text = f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")
    if decimals and text.endswith("0"):
        text = text.rstrip("0").rstrip(",")
    return text


def money_short(value, currency: str = "PLN") -> str:
    """Formats an amount for the Polish UI: 620000 -> '620 tys. zł', 1250000 -> '1,25 mln zł'."""
    if value is None:
        return "-"
    v = float(Decimal(value))
    sign = "-" if v < 0 else ""
    v = abs(v)
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    if v >= 1_000_000:
        body = f"{_num(v / 1_000_000, 2)} mln"
    elif v >= 10_000:
        body = f"{_num(v / 1_000, 0 if v >= 100_000 else 1)} tys."
    else:
        body = _num(v, 0)
    return f"{sign}{body} {symbol}"
