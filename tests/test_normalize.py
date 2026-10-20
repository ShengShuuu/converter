from datetime import datetime
from decimal import Decimal

import pytest

from converter.extraction.normalize import has_digit, parse_datetime, parse_decimal, parse_money, parse_quantity

FORMATS = ["%m/%d/%Y %H:%M:%S", "%m/%d/%Y"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2,600.00", Decimal("2600.00")),
        ("₱8,360.00", Decimal("8360.00")),
        ("PHP 1,230.50", Decimal("1230.50")),
        ("143,528.00", Decimal("143528.00")),
        ("1,000,000", Decimal("1000000")),
        ("650", Decimal("650")),
        ("-12.50", Decimal("-12.50")),
    ],
)
def test_parse_decimal_accepts_amounts(text, expected):
    result = parse_decimal(text)
    assert result == expected
    assert isinstance(result, Decimal)


@pytest.mark.parametrize("text", [None, "", "abc", "2,6O0.00", "1.234,50", "12,34.00", "1,2,3"])
def test_parse_decimal_rejects_malformed_instead_of_guessing(text):
    assert parse_decimal(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7,380.00 ₱", Decimal("7380.00")),
        ("7,380.00₱", Decimal("7380.00")),
        ("7,380.00P", Decimal("7380.00")),
        ("7,380.00 P", Decimal("7380.00")),
        ("7,380.00?", Decimal("7380.00")),
        ("7,380.00)", Decimal("7380.00")),
        ("7,380.00F", Decimal("7380.00")),
        ("7,380.00P)", Decimal("7380.00")),
        ("₱8,360.00", Decimal("8360.00")),
        ("P8,360.00", Decimal("8360.00")),
        ("650", Decimal("650")),
    ],
)
def test_parse_money_strips_adjacent_peso_glyph(text, expected):
    result = parse_money(text)
    assert result == expected
    assert isinstance(result, Decimal)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "P",
        "2,6O0.00P",  # bad digit substitution stays unparsed even with a peso glyph
        "12,34.00P",  # misplaced thousands separator
        "8.360.00P",
        "2812509)",  # real scan: "." and "," lost, so no centavos to anchor the glyph strip
        "7,380.0F",  # glyph may have eaten a centavo digit
        "7,380.00P)?",  # longer than a peso glyph misread
        "7,38P0.00",  # glyph inside the number, not adjacent
        "F7,380.00",  # leading F/?/) resemble digits, so only a leading P is a peso misread
        "2,600.00'",  # pen tick is not a peso glyph
        "B,360.00",
    ],
)
def test_parse_money_rejects_malformed_numbers(text):
    assert parse_money(text) is None


# A misread digit (5, S, 8) read as "$" mid-number must not be stripped into a wrong-but-plausible amount.
@pytest.mark.parametrize("text", ["1$00.00", "26PHP00.00", "1,5₱00.00", "1₱500.00P"])
@pytest.mark.parametrize("parse", [parse_decimal, parse_money])
def test_currency_mark_inside_number_is_rejected(parse, text):
    assert parse(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("₱1,500.00", Decimal("1500.00")),
        ("1,500.00 PHP", Decimal("1500.00")),
        ("$1,500.00", Decimal("1500.00")),
        ("1,500.00$", Decimal("1500.00")),
        ("php 1,500.00", Decimal("1500.00")),
        ("₱1,500.00 PHP", Decimal("1500.00")),
    ],
)
@pytest.mark.parametrize("parse", [parse_decimal, parse_money])
def test_currency_mark_at_edge_is_stripped(parse, text, expected):
    assert parse(text) == expected


def test_parse_decimal_stays_strict_about_peso_misreads():
    assert parse_decimal("7,380.00P") is None


def test_parse_quantity_drops_unit_of_measure():
    assert parse_quantity("1.00 Unit") == Decimal("1.00")
    assert parse_quantity("12.00 Units") == Decimal("12.00")
    assert parse_quantity("Units") is None
    assert parse_quantity("") is None


def test_parse_datetime_uses_only_explicit_formats():
    assert parse_datetime("04/22/2026 10:41:30", FORMATS) == datetime(2026, 4, 22, 10, 41, 30)
    assert parse_datetime("04/22/2026", FORMATS) == datetime(2026, 4, 22)
    assert parse_datetime("2026-04-22 10:41:30", FORMATS) is None
    assert parse_datetime("22/04/2026", FORMATS) is None


@pytest.mark.parametrize(("text", "expected"), [("1.00", True), ("x2", True), ("|", False), ("P", False), ("", False)])
def test_has_digit(text, expected):
    assert has_digit(text) is expected
