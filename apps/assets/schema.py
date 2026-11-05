"""Schema of the model's answer for an MP's asset declaration.

It mirrors sections I-XI of the official form (the annex to the act on the exercise of an
MP's and senator's mandate). Every entry carries `quote`, a verbatim fragment of the OCR
text it came from, so `verification.py` can check that the model invented nothing.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

CURRENCY_ALIASES = {
    "PLN": "PLN", "ZL": "PLN", "ZŁ": "PLN", "ZŁOTY": "PLN", "ZŁOTYCH": "PLN", "PLN.": "PLN",
    "EUR": "EUR", "EURO": "EUR", "€": "EUR",
    "USD": "USD", "$": "USD", "DOLAR": "USD", "DOLARY": "USD",
    "CHF": "CHF", "FRANK": "CHF", "FRANKI": "CHF",
    "GBP": "GBP", "£": "GBP", "FUNT": "GBP", "FUNTY": "GBP",
    "NOK": "NOK", "SEK": "SEK", "CZK": "CZK", "DKK": "DKK", "HUF": "HUF", "JPY": "JPY",
    "CAD": "CAD", "AUD": "AUD", "RON": "RON", "UAH": "UAH", "TRY": "TRY", "BGN": "BGN",
}
KNOWN_CURRENCIES = set(CURRENCY_ALIASES.values())


def parse_decimal(value) -> Decimal:
    """Lenient parsing of amounts: 1207, '1 207,50', '499.000', '150.000,00'."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError("a boolean is not a number")
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if not isinstance(value, str):
        raise ValueError(f"unsupported type: {type(value).__name__}")
    text = re.sub(r"[^\d,.\-]", "", value.replace("\u00a0", " "))
    if not re.search(r"\d", text):
        raise ValueError(f"no digits in {value!r}")
    if "," in text and "." in text:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        text = text.replace(thousands, "").replace(decimal_sep, ".")
    elif "," in text:
        text = text.replace(",", ".") if text.count(",") == 1 else text.replace(",", "")
    elif text.count(".") > 1 or re.fullmatch(r"-?\d{1,3}\.\d{3}", text):
        text = text.replace(".", "")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"cannot read a number from {value!r}") from exc


def _currency(value):
    if value is None or value == "":
        return "PLN"
    key = str(value).strip().upper()
    if key in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[key]
    if re.fullmatch(r"[A-Z]{3}", key):
        return key
    raise ValueError(f"unknown currency {value!r}; use an ISO 4217 code, e.g. PLN, EUR, CHF")


def _date(value):
    if value in (None, "", "null"):
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    m = re.fullmatch(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2}|\d{4})", text)
    if m:
        d, mth, y = int(m[1]), int(m[2]), int(m[3])
        y += 2000 if y < 100 and y < 70 else 1900 if y < 100 else 0
        return date(y, mth, d)
    return date.fromisoformat(text)


Amount = Annotated[Decimal, BeforeValidator(parse_decimal)]
Currency = Annotated[str, BeforeValidator(_currency)]
Day = Annotated[date | None, BeforeValidator(_date)]


class Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Money(Base):
    amount: Amount = Field(description="Amount as a number, dot as the decimal separator, no spaces, e.g. 62691.41")
    currency: Currency = Field(default="PLN", description="ISO 4217 code of the currency in the document, e.g. PLN, EUR, CHF")
    approximate: bool = Field(default=False, description="true when the document says 'ok.', 'około' or 'szacunkowo'")


class Entry(Base):
    quote: str = Field(
        min_length=2,
        description="Verbatim fragment of the OCR text (unedited) that this entry comes from",
    )


class MoneyEntry(Entry):
    label: str = Field(description="What it is, e.g. 'IKE', 'Polisa Nowa Perspektywa', 'cash in CHF'")
    money: Money | None = Field(default=None, description="null when the document gives no amount")


class PropertyKind(StrEnum):
    HOUSE = "HOUSE"
    APARTMENT = "APARTMENT"
    FARM = "FARM"
    LAND = "LAND"
    COMMERCIAL = "COMMERCIAL"
    GARAGE = "GARAGE"
    OTHER = "OTHER"


class RealEstate(Entry):
    kind: PropertyKind
    description: str = Field(default="", description="Description in the document's own words (no address; addresses are blacked out)")
    area_m2: Amount | None = Field(default=None, description="Area in m2; convert hectares (1 ha = 10000 m2)")
    share: str | None = Field(default=None, description="Share as a fraction, e.g. '1/2'")
    value: Money | None = Field(default=None, description="Value given in the document for THIS property; do not sum")
    legal_title: str | None = Field(default=None, description="Legal title, e.g. 'joint ownership', 'ownership'")
    under_construction: bool = False
    income: Money | None = Field(default=None, description="Revenue and income from this in the previous year")


class HoldingKind(StrEnum):
    PARTNERSHIP_CONTRIBUTION = "PARTNERSHIP_CONTRIBUTION"  # III
    STATE_COMPANY_SHARES = "STATE_COMPANY_SHARES"  # IV.1
    OTHER_COMPANY_SHARES = "OTHER_COMPANY_SHARES"  # IV.2
    STATE_COMPANY_STOCKS = "STATE_COMPANY_STOCKS"  # V.1
    OTHER_STOCKS = "OTHER_STOCKS"  # V.2


class Holding(Entry):
    kind: HoldingKind
    issuer: str = Field(description="Company or issuer, e.g. 'PZU', 'sp. z o.o.'")
    quantity: Amount | None = Field(default=None, description="Number of stocks or shares")
    value: Money | None = Field(default=None, description="Value or contribution paid in, if stated")
    income: Money | None = None


class TextEntry(Entry):
    description: str


class BusinessEntry(Entry):
    description: str = Field(description="Legal form and the business activity")
    managed_only: bool = Field(default=False, description="true for item 2 (I manage it but do not run it)")
    mode: str | None = Field(default=None, description="'in person' or 'together with other people'")
    suspended: bool = False
    income: Money | None = None


class BoardBody(StrEnum):
    MANAGEMENT = "MANAGEMENT"
    SUPERVISORY = "SUPERVISORY"
    AUDIT = "AUDIT"
    OTHER = "OTHER"


class BoardRole(Entry):
    entity: str = Field(description="Name of the company, foundation or association")
    body: BoardBody
    since: str | None = Field(default=None, description="Since when, as written in the document")
    income: Money | None = None


class IncomeEntry(Entry):
    source: str = Field(description="Income title, e.g. 'MP salary', 'untaxed allowance'")
    money: Money
    tax_note: str | None = Field(default=None, description="e.g. 'gross', 'net', 'tax base', 'tax'")


class Movable(Entry):
    description: str
    make_model: str | None = None
    year: int | None = Field(default=None, ge=1900, le=2100, description="Year of manufacture")
    ownership_note: str | None = Field(default=None, description="e.g. 'owned by Mercedes Leasing'")
    value: Money | None = None


class LiabilityKind(StrEnum):
    LOAN = "LOAN"
    MORTGAGE = "MORTGAGE"
    LEASING = "LEASING"
    CREDIT_CARD = "CREDIT_CARD"
    OTHER = "OTHER"


class Liability(Entry):
    kind: LiabilityKind
    description: str
    creditor: str | None = None
    amount: Money | None = Field(default=None, description="Amount outstanding or a limit (see is_limit)")
    is_limit: bool = Field(default=False, description="true when the amount is a limit (e.g. a card), not a debt")


class Declarant(Base):
    full_name: str = Field(description="First names and surname as in the document (with the maiden name, if present)")
    birth_date: Day = None
    birth_place: str | None = None
    occupation: str | None = Field(default=None, description="Place of employment, position or function")


class DeclarationMeta(Base):
    declaration_date: Day = Field(default=None, description="Filing date (handwritten or from the receipt stamp)")
    as_of: str | None = Field(
        default=None,
        description="As of when, e.g. 'start of the 2024 term', 'end of 2025', as in the header",
    )
    year: int | None = Field(default=None, ge=1990, le=2100, description="Year the estate is described as of")

    @field_validator("year", mode="before")
    @classmethod
    def _year_from_text(cls, v):
        if isinstance(v, str):
            m = re.search(r"(19|20)\d{2}", v)
            return int(m[0]) if m else None
        return v


class DeclarationData(Base):
    """Full structured content of the declaration. Empty lists mean 'not applicable' or 'none'."""

    declarant: Declarant
    meta: DeclarationMeta = Field(default_factory=DeclarationMeta)

    # I. Cash
    savings_pln: list[MoneyEntry] = Field(default_factory=list, description="Cash in Polish currency")
    savings_foreign: list[MoneyEntry] = Field(
        default_factory=list, description="Cash in foreign currencies; one currency = one entry"
    )
    securities: list[MoneyEntry] = Field(default_factory=list, description="Securities, IKE, IKZE, funds")
    insurance: list[MoneyEntry] = Field(default_factory=list, description="Insurance policies with a value")
    receivables: list[MoneyEntry] = Field(
        default_factory=list, description="Monetary receivables and loans granted (owed to me)"
    )
    # II. Real estate
    real_estate: list[RealEstate] = Field(default_factory=list)
    # III-V. Shares and stocks
    holdings: list[Holding] = Field(default_factory=list)
    # VI. Property bought from the State Treasury or local government at auction
    acquired_public_property: list[TextEntry] = Field(default_factory=list)
    # VII. Business activity
    business: list[BusinessEntry] = Field(default_factory=list)
    # VIII. Management boards, supervisory boards, audit committees
    board_roles: list[BoardRole] = Field(default_factory=list)
    # IX. Other income
    other_income: list[IncomeEntry] = Field(default_factory=list)
    # X. Movable property above 10,000 PLN
    movables: list[Movable] = Field(default_factory=list)
    # XI. Monetary liabilities above 10,000 PLN
    liabilities: list[Liability] = Field(default_factory=list)

    notes: str = Field(default="", description="Reading problems: illegible fragments, ambiguities")
    model_confidence: float = Field(default=0.5, ge=0, le=1, description="Your confidence in the whole reading, 0-1")

    def all_entries(self):
        """Yields (path, entry) for every entry that has a `quote`."""
        for name, value in self:
            if isinstance(value, list):
                for i, entry in enumerate(value):
                    if isinstance(entry, Entry):
                        yield f"{name}[{i}]", entry


def json_schema() -> dict:
    return DeclarationData.model_json_schema()


def strict_json_schema() -> dict:
    """Schema accepted by providers that constrain decoding (`strict: true`)."""
    return _strictify(json_schema())


def _strictify(node):
    if isinstance(node, list):
        return [_strictify(item) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return {"$ref": node["$ref"]}
    out = {key: _strictify(value) for key, value in node.items()}
    if "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out["properties"])
    return out
