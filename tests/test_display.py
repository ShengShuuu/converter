from decimal import Decimal

import pytest

from converter.app.display import is_money_path, peso


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("2900.00", "₱2,900.00"),
        (Decimal("1234567.5"), "₱1,234,567.5"),
        ("12.345", "₱12.345"),
        ("-50.00", "-₱50.00"),
        ("0", "₱0"),
        (None, ""),
        ("", ""),
        ("  ", ""),
        ("7ZOL0P", "7ZOL0P"),
        ("NaN", "NaN"),
    ],
)
def test_peso(value, shown):
    assert peso(value) == shown


def test_is_money_path():
    assert is_money_path("line.unit_price") and is_money_path("line.subtotal") and is_money_path("order.total")
    assert not is_money_path("line.quantity") and not is_money_path("order.order_date")
    assert not is_money_path(None) and not is_money_path("")
