"""Seat layout of the chamber (hemicycle) for the ECharts chart."""

import math

from ..constants import CHOICE_COLORS, club_color, club_order_key

CHOICE_ORDER = {"YES": 0, "NO": 1, "ABSTAIN": 2, "PRESENT": 3, "VOTE_VALID": 4, "ABSENT": 5}


def seat_positions(n: int, rows: int | None = None, inner: float = 0.38) -> list[tuple[float, float]]:
    """Returns n (x, y) points laid out from the left side of the hemicycle to the right."""
    if n <= 0:
        return []
    rows = rows or max(1, min(12, round(math.sqrt(n) / 1.8)))
    radii = [inner + (1 - inner) * i / max(1, rows - 1) for i in range(rows)]
    total = sum(radii)
    counts = [max(1, round(n * r / total)) for r in radii]
    counts[-1] += n - sum(counts)
    seats = []
    for r, c in zip(radii, counts):
        for j in range(c):
            angle = math.pi * (1 - j / (c - 1)) if c > 1 else math.pi / 2
            seats.append((angle, r, r * math.cos(angle), r * math.sin(angle)))
    seats.sort(key=lambda s: (-round(s[0], 6), s[1]))
    return [(round(x, 4), round(y, 4)) for _, _, x, y in seats]


def hemicycle_data(votes) -> list[dict]:
    """votes: iterable of Vote objects with mp and club already loaded."""
    ordered = sorted(
        votes,
        key=lambda v: (
            club_order_key(v.club.code if v.club else "niez."),
            CHOICE_ORDER.get(v.choice, 9),
            v.mp.last_name,
        ),
    )
    positions = seat_positions(len(ordered))
    data = []
    for (x, y), v in zip(positions, ordered):
        code = v.club.code if v.club else "niez."
        data.append({
            "value": [x, y],
            "name": v.mp.full_name,
            "club": code,
            "choice": v.get_choice_display(),
            "choiceColor": CHOICE_COLORS.get(v.choice, "#a1a1aa"),
            "clubColor": club_color(code),
            "url": v.mp.get_absolute_url(),
        })
    return data
