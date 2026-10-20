from datetime import datetime
from decimal import Decimal

import pytest

from converter.app.field_input import format_field_value, parse_field_input


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("total", "8,360.00", Decimal("8360.00")),
        ("subtotal", " ₱2,600.00 ", Decimal("2600.00")),
        ("unit_price", "1234.5", Decimal("1234.5")),
        ("quantity", "2", Decimal("2")),
        ("order_date", "2026-04-22T10:41:30", datetime(2026, 4, 22, 10, 41, 30)),
        ("order_date", "2026-04-22T10:41", datetime(2026, 4, 22, 10, 41)),
        ("order_date", "2026-04-22 10:41", datetime(2026, 4, 22, 10, 41)),
        ("order_date", "2026-04-22", datetime(2026, 4, 22)),
        ("customer", "  ACME TRADING CORP. ", "ACME TRADING CORP."),
        ("taxes", "Output Tax (12%)", "Output Tax (12%)"),
    ],
)
def test_valid_input_parses_to_the_field_type(field, raw, expected):
    parsed = parse_field_input(field, raw)
    assert parsed.error is None
    assert parsed.value == expected and type(parsed.value) is type(expected)


@pytest.mark.parametrize("field", ["total", "order_date", "customer"])
def test_blank_input_clears_the_field(field):
    assert parse_field_input(field, "   ") == parse_field_input(field, "")
    assert parse_field_input(field, "").value is None and parse_field_input(field, "").error is None


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        ("total", "12,34.50"),
        ("subtotal", "7ZOL0P"),
        ("unit_price", "1.234,50"),
        ("order_date", "04/22/2026"),
        ("order_date", "2026-02-30 10:00"),
        ("order_date", "yesterday"),
    ],
)
def test_invalid_input_returns_an_error_and_no_value(field, raw):
    parsed = parse_field_input(field, raw)
    assert parsed.value is None and parsed.error


@pytest.mark.parametrize(
    ("field", "value"),
    [("total", Decimal("8360.00")), ("order_date", datetime(2026, 4, 22, 10, 41, 30)), ("customer", "ACME")],
)
def test_formatted_value_parses_back_to_itself(field, value):
    assert parse_field_input(field, format_field_value(value)).value == value
