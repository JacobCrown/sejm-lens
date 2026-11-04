"""Readable summary of an MP's estate: totals, structure, year-over-year changes and insights."""

from dataclasses import dataclass, field
from decimal import Decimal

from apps.core.formatting import money_short

from ..models import (
    OTHER_ASSET_CATEGORIES,
    PROPERTY_CATEGORIES,
    SAVINGS_CATEGORIES,
    AssetCategory,
    AssetDeclaration,
    AssetItem,
)

CATEGORY_COLORS = {
    AssetCategory.CASH_PLN: "#0ea5e9",
    AssetCategory.CASH_FOREIGN: "#6366f1",
    AssetCategory.SECURITIES: "#8b5cf6",
    AssetCategory.INSURANCE: "#ec4899",
    AssetCategory.RECEIVABLE: "#84cc16",
    AssetCategory.REAL_ESTATE: "#f59e0b",
    AssetCategory.SHARES: "#14b8a6",
    AssetCategory.VEHICLE: "#64748b",
    AssetCategory.OTHER: "#a3a3a3",
}

KIND_PRIORITY = {AssetDeclaration.Kind.END: 3, AssetDeclaration.Kind.ANNUAL: 2, AssetDeclaration.Kind.START: 1}
VISIBLE_STATUSES = (AssetDeclaration.Status.EXTRACTED, AssetDeclaration.Status.NEEDS_REVIEW)
ZERO = Decimal(0)


def describe_item(item: AssetItem) -> str:
    parts = [item.label]
    if item.area_m2:
        area = item.area_m2
        if area >= 10_000:
            parts.append(f"{(area / 10_000):.2f} ha".replace(".", ","))
        else:
            parts.append(f"{area.normalize():f} m²")
    text = " ".join(parts)
    if item.value is not None:
        if item.category == AssetCategory.LIABILITY:
            text += f" - do spłaty {money_short(item.value, item.currency)}"
        elif item.category == AssetCategory.INCOME:
            text += f": {money_short(item.value, item.currency)}"
        else:
            text += f" o wartości {money_short(item.value, item.currency)}"
        if item.currency != "PLN" and item.value_pln is not None:
            text += f" (ok. {money_short(item.value_pln)})"
    return text


@dataclass
class WealthSummary:
    declaration: AssetDeclaration
    savings: Decimal = ZERO
    real_estate: Decimal = ZERO
    other_assets: Decimal = ZERO
    liabilities: Decimal = ZERO
    income: Decimal = ZERO
    structure: list[dict] = field(default_factory=list)
    groups: list[dict] = field(default_factory=list)
    extras: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def year(self) -> int:
        return self.declaration.year

    @property
    def assets_total(self) -> Decimal:
        return self.savings + self.real_estate + self.other_assets

    @property
    def net_worth(self) -> Decimal:
        return self.assets_total - self.liabilities


BOARD_BODIES = {
    "MANAGEMENT": "zarząd",
    "SUPERVISORY": "rada nadzorcza",
    "AUDIT": "komisja rewizyjna",
    "OTHER": "inna funkcja",
}


def extras_from_structured(structured: dict | None) -> list[dict]:
    """Non-monetary facts from the form: business activity, board roles, property bought at auction."""
    if not structured:
        return []
    out = []
    business = []
    for b in structured.get("business") or []:
        tags = [t for t in ("zarządzam" if b.get("managed_only") else "prowadzę", b.get("mode"),
                            "zawieszona" if b.get("suspended") else "") if t]
        business.append(f"{b['description']} ({', '.join(tags)})")
    if business:
        out.append({"title": "Działalność gospodarcza", "lines": business})
    roles = []
    for r in structured.get("board_roles") or []:
        since = f", od {r['since']}" if r.get("since") else ""
        roles.append(f"{r['entity']}: {BOARD_BODIES.get(r['body'], r['body'])}{since}")
    if roles:
        out.append({"title": "Funkcje w spółkach i fundacjach", "lines": roles})
    acquired = [a["description"] for a in structured.get("acquired_public_property") or []]
    if acquired:
        out.append({"title": "Mienie nabyte od państwa lub samorządu w przetargu", "lines": acquired})
    return out


# Notes that say nothing about reading quality and are not useful on the profile.
HIDDEN_NOTE_PREFIXES = ("Kurs ", "Brak wartości", "Dane demonstracyjne", "Rate ", "Missing value", "Demo data")


def summarize(declaration: AssetDeclaration) -> WealthSummary:
    s = WealthSummary(declaration=declaration)
    s.extras = extras_from_structured(declaration.structured)
    if declaration.status == AssetDeclaration.Status.NEEDS_REVIEW:
        s.issues = [
            line for line in (declaration.notes or "").splitlines()
            if line.startswith(("BŁĄD", "Uwaga")) and not line.startswith(HIDDEN_NOTE_PREFIXES)
        ]
    by_cat: dict[str, list[AssetItem]] = {}
    for item in declaration.items.all():
        by_cat.setdefault(item.category, []).append(item)
        v = item.value_pln or ZERO
        if item.category in SAVINGS_CATEGORIES:
            s.savings += v
        elif item.category in PROPERTY_CATEGORIES:
            s.real_estate += v
        elif item.category in OTHER_ASSET_CATEGORIES:
            s.other_assets += v
        elif item.category == AssetCategory.LIABILITY:
            s.liabilities += v
        elif item.category == AssetCategory.INCOME:
            s.income += v

    for cat in AssetCategory:
        items = by_cat.get(cat.value)
        if not items:
            continue
        total = sum((i.value_pln or ZERO for i in items), ZERO)
        s.groups.append({
            "category": cat,
            "label": cat.label,
            "total": total,
            "items": [{"item": i, "text": describe_item(i)} for i in items],
        })
        if cat in CATEGORY_COLORS and total > 0:
            s.structure.append({"name": cat.label, "value": float(total), "color": CATEGORY_COLORS[cat]})
    if s.assets_total > 0:
        for entry in s.structure:
            entry["pct"] = 100 * entry["value"] / float(s.assets_total)
    return s


def timeline(mp) -> list[WealthSummary]:
    """One summary per year (when a year has several declarations, the latest kind wins)."""
    best: dict[int, AssetDeclaration] = {}
    declarations = mp.declarations.filter(status__in=VISIBLE_STATUSES).prefetch_related("items", "runs")
    for d in declarations:
        cur = best.get(d.year)
        if cur is None or KIND_PRIORITY[d.kind] > KIND_PRIORITY[cur.kind]:
            best[d.year] = d
    return [summarize(best[y]) for y in sorted(best)]


@dataclass
class ProcessingCost:
    """Cost of computing an MP's financial data: the sum over the declarations shown on the profile."""

    declarations: int = 0
    priced: int = 0
    total_usd: Decimal | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    models: list[str] = field(default_factory=list)

    @property
    def unpriced(self) -> int:
        return self.declarations - self.priced

    @property
    def total_text(self) -> str:
        if self.total_usd is None:
            return ""
        return f"{self.total_usd:.6f}".rstrip("0").rstrip(".").replace(".", ",") + " USD"


def processing_cost(series: list[WealthSummary]) -> ProcessingCost | None:
    """Sums the cost of successful model runs. Demo data and manual edits cost nothing, so they are skipped."""
    cost = ProcessingCost()
    models: set[str] = set()
    for s in series:
        d = s.declaration
        run = d.latest_successful_run
        if d.is_demo or run is None or run.extractor == "mock":
            continue
        cost.declarations += 1
        if run.model:
            models.add(run.model)
        if run.prompt_tokens is not None:
            cost.prompt_tokens += run.prompt_tokens
            cost.completion_tokens += run.completion_tokens or 0
        if run.cost_usd is not None:
            cost.priced += 1
            cost.total_usd = (cost.total_usd or ZERO) + run.cost_usd
    if not cost.declarations:
        return None
    cost.models = sorted(models)
    return cost


def _pct_change(new: Decimal, old: Decimal) -> float | None:
    if not old:
        return None
    return float((new - old) / abs(old) * 100)


def insights(series: list[WealthSummary]) -> list[dict]:
    """Short, deterministic insights in plain language (Polish, shown on the profile)."""
    if not series:
        return []
    latest = series[-1]
    out = []
    if latest.assets_total > 0:
        parts = {
            "nieruchomości": latest.real_estate,
            "oszczędności": latest.savings,
            "pozostałe składniki": latest.other_assets,
        }
        name, value = max(parts.items(), key=lambda kv: kv[1])
        share = float(value / latest.assets_total * 100)
        out.append({"tone": "neutral", "text": f"Największą część majątku stanowią {name} ({share:.0f}%)."})
    if len(series) >= 2:
        prev = series[-2]
        diff = latest.net_worth - prev.net_worth
        pct = _pct_change(latest.net_worth, prev.net_worth)
        direction = "wzrósł" if diff >= 0 else "spadł"
        pct_txt = f" ({pct:+.0f}%)" if pct is not None else ""
        out.append({
            "tone": "up" if diff >= 0 else "down",
            "text": (
                f"Majątek netto {direction} o {money_short(abs(diff))}{pct_txt} "
                f"między {prev.year} a {latest.year} rokiem."
            ),
        })
        debt_diff = latest.liabilities - prev.liabilities
        if prev.liabilities and debt_diff < 0:
            out.append({"tone": "up", "text": f"Zadłużenie zmniejszyło się o {money_short(-debt_diff)}."})
        elif debt_diff > 0:
            out.append({"tone": "down", "text": f"Zadłużenie wzrosło o {money_short(debt_diff)}."})
    if latest.liabilities > 0 and latest.assets_total > 0:
        ratio = float(latest.liabilities / latest.assets_total * 100)
        out.append({"tone": "neutral", "text": f"Zobowiązania odpowiadają {ratio:.0f}% wartości majątku."})
    return out


def chart_series(series: list[WealthSummary]) -> dict:
    return {
        "years": [str(s.year) for s in series],
        "savings": [float(s.savings) for s in series],
        "real_estate": [float(s.real_estate) for s in series],
        "other": [float(s.other_assets) for s in series],
        "liabilities": [-float(s.liabilities) for s in series],
        "net": [float(s.net_worth) for s in series],
    }
