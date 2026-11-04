import math

import pytest

from apps.core.formatting import money_short
from apps.parliament.services.hemicycle import seat_positions


@pytest.mark.parametrize("n", [1, 5, 100, 460])
def test_seat_positions_count_and_shape(n):
    seats = seat_positions(n)
    assert len(seats) == n
    assert all(y >= -1e-9 and math.hypot(x, y) <= 1.0001 for x, y in seats)


def test_seats_go_left_to_right():
    seats = seat_positions(460)
    assert seats[0][0] < 0 < seats[-1][0]


@pytest.mark.parametrize(
    "value, currency, expected",
    [
        (850, "PLN", "850 zł"),
        (42_900, "PLN", "42,9 tys. zł"),
        (620_000, "PLN", "620 tys. zł"),
        (1_250_000, "PLN", "1,25 mln zł"),
        (1_000_000, "PLN", "1 mln zł"),
        (-41_700, "PLN", "-41,7 tys. zł"),
        (24_300, "EUR", "24,3 tys. €"),
        (None, "PLN", "-"),
    ],
)
def test_money_short(value, currency, expected):
    assert money_short(value, currency) == expected
