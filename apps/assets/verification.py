"""Checks the model's answer against the OCR text.

Schema validation (Pydantic) only checks the shape. This module checks the substance:
whether every amount, quantity and quote really occurs in the document, whether the currency
matches the quote, whether the declarant is the right MP and whether the values are plausible.
Nothing here calls an LLM.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal

from .schema import KNOWN_CURRENCIES, DeclarationData, Money

ERROR, WARNING = "error", "warning"

QUOTE_ERROR_BELOW = 0.60
QUOTE_WARN_BELOW = 0.85
MAX_AMOUNT = Decimal("1000000000")
MAX_AREA_M2 = Decimal("50000000")
OCR_LOW_CONFIDENCE = 0.80

CURRENCY_WORDS = {
    "PLN": r"\bpln\b|\bz[łl]\b|z[łl]ot",
    "EUR": r"\beur\b|\beuro\b|€",
    "USD": r"\busd\b|dolar|\$",
    "CHF": r"\bchf\b|frank",
    "GBP": r"\bgbp\b|funt|£",
    "NOK": r"\bnok\b",
    "SEK": r"\bsek\b",
    "CZK": r"\bczk\b",
}
# A word boundary does not work for digits glued to a currency (5954CHF), hence a separate pattern.
CURRENCY_GLUED = {code: re.compile(rf"\d\s?{code}", re.I) for code in CURRENCY_WORDS}

MULTIPLIER_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(mln|milion\w*|mld|miliard\w*|tys\.?|tysi[aą]c\w*)(?![a-ząćęłńóśźż])", re.I
)
MULTIPLIERS = {"tys": 10**3, "tysi": 10**3, "mln": 10**6, "milion": 10**6, "mld": 10**9, "miliard": 10**9}

NUMBER_RE = re.compile(r"\d{1,3}(?:[ \u00a0.]\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d+)?")

# Error codes the model can fix after feedback (the rest need a human).
FIXABLE = {"ungrounded_amount", "ungrounded_quantity", "quote_not_found", "currency_mismatch"}


@dataclass
class Issue:
    level: str
    code: str
    path: str
    message: str


@dataclass
class VerificationReport:
    issues: list[Issue] = field(default_factory=list)
    checks: int = 0
    passed: int = 0
    ocr_confidence: float | None = None
    model_confidence: float = 0.0
    confidence: float = 0.0

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == WARNING]

    @property
    def fixable_errors(self) -> list[Issue]:
        return [i for i in self.errors if i.code in FIXABLE]

    @property
    def grounded_ratio(self) -> float:
        return self.passed / self.checks if self.checks else 0.0

    def messages(self) -> list[str]:
        return [f"{'BŁĄD' if i.level == ERROR else 'Uwaga'} [{i.path}]: {i.message}" for i in self.issues]

    def to_dict(self) -> dict:
        return {
            "issues": [asdict(i) for i in self.issues],
            "checks": self.checks,
            "passed": self.passed,
            "grounded_ratio": round(self.grounded_ratio, 3),
            "ocr_confidence": self.ocr_confidence,
            "model_confidence": self.model_confidence,
            "confidence": round(self.confidence, 3),
        }


def normalize(text: str) -> str:
    """Lower case, without diacritics or punctuation, single spaces."""
    text = text.replace("ł", "l").replace("Ł", "L")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _trigrams(text: str) -> set[str]:
    t = f" {text} "
    return {t[i : i + 3] for i in range(len(t) - 2)}


def quote_coverage(quote: str, ocr_trigrams: set[str]) -> float:
    q = _trigrams(normalize(quote))
    return len(q & ocr_trigrams) / len(q) if q else 0.0


def number_forms(ocr_text: str) -> set[str]:
    """Every number from the OCR text as digits only (with and without the fractional part)."""
    forms: set[str] = set()
    for m in NUMBER_RE.finditer(ocr_text):
        token = m.group(0).replace("\u00a0", " ")
        digits = re.sub(r"\D", "", token)
        forms.add(digits)
        forms.add(digits.lstrip("0") or "0")
        integer_part = re.split(r"[,.]", token)
        if len(integer_part) > 1 and not re.fullmatch(r"\d{3}", integer_part[-1]):
            forms.add(re.sub(r"\D", "", "".join(integer_part[:-1])))
        if token.count(".") == 1 and re.fullmatch(r"\d+\.\d{3}", token):
            forms.add(token.split(".")[0])
    for m in MULTIPLIER_RE.finditer(ocr_text):
        word = m.group(2).lower().rstrip(".")
        factor = next((f for prefix, f in MULTIPLIERS.items() if word.startswith(prefix)), None)
        if factor:
            forms |= amount_forms(Decimal(m.group(1).replace(",", ".")) * factor)
    return forms


def amount_forms(value: Decimal) -> set[str]:
    value = abs(value)
    forms = {str(int(value))}
    if value != value.to_integral_value():
        forms.add(re.sub(r"\D", "", f"{value:.2f}"))
        forms.add(re.sub(r"\D", "", str(value.normalize())))
    return forms


def _currencies_in(text: str) -> set[str]:
    found = {code for code, pat in CURRENCY_WORDS.items() if re.search(pat, text, re.I)}
    found |= {code for code, pat in CURRENCY_GLUED.items() if pat.search(text)}
    return found


def _iter_money(path: str, entry) -> list[tuple[str, Money]]:
    out = []
    for name, value in entry:
        if isinstance(value, Money):
            out.append((f"{path}.{name}", value))
    return out


def _normalize_person(text: str) -> list[str]:
    return [t for t in normalize(text).split() if len(t) > 1]


def _token_matches(token: str, candidates: list[str]) -> bool:
    if token in candidates:
        return True
    return any(len(c) > 3 and len(token) > 3 and quote_coverage(token, _trigrams(c)) >= 0.75 for c in candidates)


def verify(
    data: DeclarationData,
    ocr_text: str,
    *,
    ocr_confidence: float | None = None,
    expected_name: str | None = None,
    expected_year: int | None = None,
) -> VerificationReport:
    report = VerificationReport(ocr_confidence=ocr_confidence, model_confidence=data.model_confidence)
    norm_ocr = normalize(ocr_text)
    ocr_trigrams = _trigrams(norm_ocr)
    ocr_numbers = number_forms(ocr_text)

    def check(ok: bool) -> bool:
        report.checks += 1
        report.passed += int(ok)
        return ok

    def add(level: str, code: str, path: str, message: str):
        report.issues.append(Issue(level, code, path, message))

    if len(norm_ocr) < 80:
        add(ERROR, "ocr_empty", "ocr", "Tekst OCR jest pusty lub zbyt krótki, żeby cokolwiek zweryfikować")
        report.confidence = 0.0
        return report

    # Declarant
    declared = _normalize_person(data.declarant.full_name)
    if expected_name:
        expected = _normalize_person(expected_name)
        if not check(bool(expected) and all(_token_matches(t, declared) for t in expected)):
            add(
                ERROR, "wrong_declarant", "declarant.full_name",
                f"Deklarant '{data.declarant.full_name}' nie pasuje do posła '{expected_name}'. Możliwy zły plik",
            )
    if declared and not check(all(_token_matches(t, [w for w in norm_ocr.split() if len(w) > 1]) for t in declared)):
        add(WARNING, "declarant_not_in_ocr", "declarant.full_name", "Nazwiska nie widać w tekście OCR")

    # Year
    year = data.meta.year or (data.meta.declaration_date.year if data.meta.declaration_date else None)
    if expected_year and year and abs(year - expected_year) > 1:
        add(WARNING, "year_mismatch", "meta.year", f"Rok w dokumencie ({year}) różni się od oczekiwanego ({expected_year})")
    if data.meta.declaration_date and data.meta.declaration_date > date.today():
        add(WARNING, "future_date", "meta.declaration_date", "Data złożenia jest w przyszłości")

    entries = list(data.all_entries())
    if not entries:
        add(WARNING, "no_entries", "data", "Model nie zwrócił żadnego wpisu (puste oświadczenie?)")

    seen: set[tuple] = set()
    for path, entry in entries:
        # 1. The quote must occur in the OCR text
        coverage = quote_coverage(entry.quote, ocr_trigrams)
        if coverage < QUOTE_ERROR_BELOW:
            check(False)
            add(ERROR, "quote_not_found", path, f"Cytat nie występuje w tekście OCR (zgodność {coverage:.0%}): {entry.quote[:80]!r}")
        elif coverage < QUOTE_WARN_BELOW:
            check(False)
            add(WARNING, "quote_fuzzy", path, f"Cytat tylko częściowo zgadza się z OCR (zgodność {coverage:.0%}): {entry.quote[:80]!r}")
        else:
            check(True)

        quote_currencies = _currencies_in(entry.quote)
        quote_numbers = number_forms(entry.quote)

        # 2. Every amount must occur in the document (preferably in the entry's quote), and the currency must match the quote
        for mpath, money in _iter_money(path, entry):
            if money.amount < 0:
                add(ERROR, "negative_amount", mpath, f"Ujemna kwota: {money.amount}")
            if money.amount > MAX_AMOUNT:
                add(ERROR, "implausible_amount", mpath, f"Nierealnie wysoka kwota: {money.amount}")
            if money.currency not in KNOWN_CURRENCIES:
                add(WARNING, "unknown_currency", mpath, f"Niecodzienna waluta: {money.currency}")
            forms = amount_forms(money.amount)
            if forms & quote_numbers:
                check(True)
            elif forms & ocr_numbers:
                check(True)
                add(
                    WARNING, "amount_outside_quote", mpath,
                    f"Kwota {money.amount} jest w dokumencie, ale nie w cytacie tego wpisu. Możliwe, że pochodzi z innej pozycji",
                )
            else:
                check(False)
                add(
                    ERROR, "ungrounded_amount", mpath,
                    f"Kwoty {money.amount} {money.currency} nie ma w tekście OCR (halucynacja, suma kilku pozycji "
                    "albo błąd odczytu cyfr)",
                )
            if quote_currencies and not check(money.currency in quote_currencies):
                add(
                    ERROR, "currency_mismatch", mpath,
                    f"Waluta {money.currency} nie pasuje do cytatu, który wspomina: {', '.join(sorted(quote_currencies))}",
                )

        # 3. Quantities (stocks, shares) must also come from the document
        quantity = getattr(entry, "quantity", None)
        if quantity is not None:
            if not check(bool(amount_forms(quantity) & ocr_numbers)):
                add(ERROR, "ungrounded_quantity", f"{path}.quantity", f"Liczby {quantity} nie ma w tekście OCR")

        area = getattr(entry, "area_m2", None)
        if area is not None and (area <= 0 or area > MAX_AREA_M2):
            add(ERROR, "implausible_area", f"{path}.area_m2", f"Nierealna powierzchnia: {area} m²")

        share = getattr(entry, "share", None)
        if share and not re.fullmatch(r"\d{1,3}\s*/\s*\d{1,3}", share.strip()):
            add(WARNING, "bad_share", f"{path}.share", f"Udział powinien być ułamkiem, np. '1/2' (jest {share!r})")

        key = (type(entry).__name__, normalize(entry.quote), tuple(sorted(m.amount for _, m in _iter_money(path, entry))))
        if key in seen:
            add(WARNING, "duplicate_entry", path, "Ten sam wpis występuje więcej niż raz")
        seen.add(key)

    if ocr_confidence is not None and ocr_confidence < OCR_LOW_CONFIDENCE:
        add(WARNING, "ocr_low_confidence", "ocr", f"Niska pewność OCR ({ocr_confidence:.0%}); część cyfr mogła zostać źle odczytana")

    report.confidence = _confidence(report, data.model_confidence)
    return report


def _confidence(report: VerificationReport, model_confidence: float) -> float:
    """Overall confidence: match to the OCR text (50%), OCR quality (30%), the model's own estimate (20%), minus penalties."""
    grounded = report.grounded_ratio if report.checks else 0.0
    ocr = report.ocr_confidence if report.ocr_confidence is not None else 0.85
    score = 0.5 * grounded + 0.3 * ocr + 0.2 * model_confidence
    score -= 0.15 * len(report.errors) + 0.02 * len(report.warnings)
    return max(0.0, min(1.0, score))
