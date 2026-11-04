"""Maps `DeclarationData` (a faithful image of the form) to flat `AssetItem` rows."""

from .extractors.base import ExtractedItem
from .models import AssetCategory
from .schema import DeclarationData, HoldingKind, LiabilityKind, Money, PropertyKind

PROPERTY_LABELS = {
    PropertyKind.HOUSE: "Dom",
    PropertyKind.APARTMENT: "Mieszkanie",
    PropertyKind.FARM: "Gospodarstwo rolne",
    PropertyKind.LAND: "Działka",
    PropertyKind.COMMERCIAL: "Lokal użytkowy",
    PropertyKind.GARAGE: "Garaż lub miejsce parkingowe",
    PropertyKind.OTHER: "Nieruchomość",
}
HOLDING_LABELS = {
    HoldingKind.PARTNERSHIP_CONTRIBUTION: "Wkład w spółce",
    HoldingKind.STATE_COMPANY_SHARES: "Udziały (spółka z udziałem państwa)",
    HoldingKind.OTHER_COMPANY_SHARES: "Udziały",
    HoldingKind.STATE_COMPANY_STOCKS: "Akcje (spółka z udziałem państwa)",
    HoldingKind.OTHER_STOCKS: "Akcje",
}
LIABILITY_LABELS = {
    LiabilityKind.LOAN: "Pożyczka lub kredyt",
    LiabilityKind.MORTGAGE: "Kredyt hipoteczny",
    LiabilityKind.LEASING: "Leasing",
    LiabilityKind.CREDIT_CARD: "Karta kredytowa",
    LiabilityKind.OTHER: "Zobowiązanie",
}


def _value(money: Money | None):
    return (money.amount, money.currency) if money else (None, "PLN")


def _join(*parts) -> str:
    return " · ".join(p for p in parts if p)


def to_items(data: DeclarationData) -> list[ExtractedItem]:
    items: list[ExtractedItem] = []

    def money_entries(entries, category, default_label):
        for e in entries:
            value, currency = _value(e.money)
            if e.money and e.money.approximate:
                currency_note = "wartość szacunkowa"
            else:
                currency_note = ""
            items.append(ExtractedItem(
                category=category, label=e.label or default_label, description=currency_note,
                value=value, currency=currency,
            ))

    money_entries(data.savings_pln, AssetCategory.CASH_PLN, "Środki w złotych")
    money_entries(data.savings_foreign, AssetCategory.CASH_FOREIGN, "Środki w walucie obcej")
    money_entries(data.securities, AssetCategory.SECURITIES, "Papiery wartościowe")
    money_entries(data.insurance, AssetCategory.INSURANCE, "Polisa ubezpieczeniowa")
    money_entries(data.receivables, AssetCategory.RECEIVABLE, "Należność")

    for r in data.real_estate:
        value, currency = _value(r.value)
        approx = "wartość szacunkowa" if r.value and r.value.approximate else ""
        items.append(ExtractedItem(
            category=AssetCategory.REAL_ESTATE,
            label=PROPERTY_LABELS[r.kind] + (" w budowie" if r.under_construction else ""),
            description=_join(r.description, approx),
            value=value, currency=currency, area_m2=r.area_m2,
            ownership=_join(r.legal_title, f"udział {r.share}" if r.share else ""),
        ))
        if r.income:
            items.append(ExtractedItem(
                category=AssetCategory.INCOME, label=f"Dochód z: {PROPERTY_LABELS[r.kind].lower()}",
                value=r.income.amount, currency=r.income.currency,
            ))

    for h in data.holdings:
        value, currency = _value(h.value)
        qty = f"{h.quantity.normalize():f} szt." if h.quantity is not None else ""
        items.append(ExtractedItem(
            category=AssetCategory.SHARES, label=f"{HOLDING_LABELS[h.kind]}: {h.issuer}",
            description=qty, value=value, currency=currency,
        ))
        if h.income:
            items.append(ExtractedItem(
                category=AssetCategory.INCOME, label=f"Dochód z: {h.issuer}",
                value=h.income.amount, currency=h.income.currency,
            ))

    for b in data.business:
        if b.income:
            items.append(ExtractedItem(
                category=AssetCategory.INCOME, label=f"Działalność: {b.description}"[:200],
                value=b.income.amount, currency=b.income.currency,
            ))
    for role in data.board_roles:
        if role.income:
            items.append(ExtractedItem(
                category=AssetCategory.INCOME, label=f"Funkcja w: {role.entity}"[:200],
                value=role.income.amount, currency=role.income.currency,
            ))

    for inc in data.other_income:
        items.append(ExtractedItem(
            category=AssetCategory.INCOME, label=inc.source, description=inc.tax_note or "",
            value=inc.money.amount, currency=inc.money.currency,
        ))

    for m in data.movables:
        value, currency = _value(m.value)
        items.append(ExtractedItem(
            category=AssetCategory.VEHICLE, label=m.make_model or m.description,
            description=_join(m.description if m.make_model else "", f"rok produkcji {m.year}" if m.year else ""),
            ownership=m.ownership_note or "", value=value, currency=currency,
        ))

    for li in data.liabilities:
        value, currency = _value(li.amount)
        items.append(ExtractedItem(
            category=AssetCategory.LIABILITY, label=LIABILITY_LABELS[li.kind],
            description=_join(li.description, f"wierzyciel: {li.creditor}" if li.creditor else "",
                              f"limit {li.amount.amount.normalize():f} {li.amount.currency} (nie jest to zadłużenie)"
                              if li.is_limit and li.amount else ""),
            # A card limit is not a debt. It is shown in the description and excluded from liabilities.
            value=None if li.is_limit else value, currency=currency,
        ))
    return items
