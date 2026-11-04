import hashlib

# Left-to-right order of the chamber, used by the hemicycle chart.
CLUB_ORDER = [
    "Razem",
    "Lewica",
    "KO",
    "Centrum",
    "Polska2050",
    "Polska2050-TD",
    "Demokracja",
    "PSL-TD",
    "RozwojPlus",
    "PiS",
    "Konfederacja",
    "Konfederacja_KP",
    "Republikanie",
]

CLUB_COLORS = {
    "Razem": "#9d174d",
    "Lewica": "#dc2626",
    "KO": "#ea580c",
    "Centrum": "#0d9488",
    "Polska2050": "#ca8a04",
    "Polska2050-TD": "#ca8a04",
    "Demokracja": "#64748b",
    "PSL-TD": "#16a34a",
    "RozwojPlus": "#0284c7",
    "PiS": "#1e3a8a",
    "Konfederacja": "#854d0e",
    "Konfederacja_KP": "#57534e",
    "Republikanie": "#4338ca",
    "niez.": "#9ca3af",
}

_FALLBACK_PALETTE = ["#7c3aed", "#0891b2", "#be123c", "#4d7c0f", "#c2410c", "#4338ca"]

INDEPENDENT_CODES = {"niez."}


def club_color(code: str) -> str:
    if code in CLUB_COLORS:
        return CLUB_COLORS[code]
    idx = int(hashlib.md5(code.encode()).hexdigest(), 16) % len(_FALLBACK_PALETTE)
    return _FALLBACK_PALETTE[idx]


def club_order_key(code: str) -> tuple[int, str]:
    if code in INDEPENDENT_CODES:
        return (len(CLUB_ORDER) + 1, code)
    if code in CLUB_ORDER:
        return (CLUB_ORDER.index(code), code)
    return (len(CLUB_ORDER), code)


CHOICE_COLORS = {
    "YES": "#16a34a",
    "NO": "#dc2626",
    "ABSTAIN": "#f59e0b",
    "ABSENT": "#d4d4d8",
    "PRESENT": "#94a3b8",
    "VOTE_VALID": "#6366f1",
}
