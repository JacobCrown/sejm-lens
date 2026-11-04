from urllib.parse import urlencode

from django import template

from apps.core.formatting import money_short
from apps.parliament.constants import CHOICE_COLORS, club_color

register = template.Library()

CHOICE_LABELS = {
    "YES": "Za",
    "NO": "Przeciw",
    "ABSTAIN": "Wstrzymał się",
    "ABSENT": "Nieobecny",
    "PRESENT": "Nie głosował",
    "VOTE_VALID": "Głos na liście",
    "VOTE_INVALID": "Głos nieważny",
    "": "Brak wyraźnej większości",
}

CHOICE_BADGES = {
    "YES": "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    "NO": "bg-red-50 text-red-700 ring-red-600/20",
    "ABSTAIN": "bg-amber-50 text-amber-700 ring-amber-600/20",
    "ABSENT": "bg-zinc-100 text-zinc-600 ring-zinc-500/20",
    "PRESENT": "bg-slate-100 text-slate-600 ring-slate-500/20",
}


@register.filter
def money(value, currency="PLN"):
    return money_short(value, currency or "PLN")


@register.filter
def pct(value, decimals=1):
    if value is None:
        return "-"
    return f"{value:.{int(decimals)}f}".replace(".", ",") + "%"


@register.filter
def ratio_pct(value, decimals=1):
    """0.973 -> '97,3%'."""
    return pct(None if value is None else value * 100, decimals)


@register.filter
def choice_label(value):
    return CHOICE_LABELS.get(value, value)


@register.filter
def choice_badge(value):
    return CHOICE_BADGES.get(value, "bg-indigo-50 text-indigo-700 ring-indigo-600/20")


@register.filter
def choice_color(value):
    return CHOICE_COLORS.get(value, "#a1a1aa")


@register.filter
def clubcolor(code):
    return club_color(code or "niez.")


@register.filter
def width(value, total):
    try:
        return f"{100 * float(value) / float(total):.2f}" if float(total) else "0"
    except (TypeError, ValueError):
        return "0"


@register.simple_tag(takes_context=True)
def query(context, **kwargs):
    """Aktualny query string z podmienionymi parametrami (np. dla paginacji)."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    return "?" + urlencode(params, doseq=True)
