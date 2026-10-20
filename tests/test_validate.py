from decimal import Decimal

from converter.extraction.validate import validate_line, validate_order
from converter.models import Order, OrderLine


def line(quantity, unit_price, subtotal, confidence=None):
    return OrderLine(
        product="X",
        quantity=Decimal(quantity) if quantity else None,
        unit_price=Decimal(unit_price) if unit_price else None,
        subtotal=Decimal(subtotal) if subtotal else None,
        confidence=confidence or {},
    )


def test_amount_cross_check_within_tolerance_is_not_flagged():
    assert validate_line(line("3", "33.33", "100.00"), 0.6).flags == []


def test_amount_cross_check_beyond_tolerance_is_flagged():
    assert validate_line(line("3", "33.33", "100.01"), 0.6).flags == ["amount_mismatch"]


def test_blank_subtotal_is_valid_not_flagged():
    assert validate_line(line("1", "650", None), 0.6).flags == []


def test_line_without_product_is_flagged():
    phantom = OrderLine(subtotal=Decimal("0"), confidence={"subtotal": 0.9})
    # assert validate_line(phantom, 0.6).flags == ["not_found:product"]
    assert validate_line(phantom, 0.6).flags == ["not_found:product", "not_found:quantity", "not_found:unit_price"]


def test_line_missing_quantity_or_unit_price_is_flagged():
    assert validate_line(line(None, "650", None), 0.6).flags == ["not_found:quantity"]
    assert validate_line(line("1", None, None), 0.6).flags == ["not_found:unit_price"]


def test_unparsed_value_is_not_also_flagged_not_found():
    unreadable = OrderLine(product="X", quantity=Decimal("1"), flags=["unparsed:unit_price"])
    assert validate_line(unreadable, 0.6).flags == ["unparsed:unit_price"]


def test_unmatched_column_is_not_flagged_per_line():
    assert validate_line(line("1", None, None), 0.6, unmatched_columns={"unit_price"}).flags == []


def test_low_confidence_fields_are_flagged():
    flagged = validate_line(line("1", "650", "650", {"quantity": 0.95, "unit_price": 0.41}), 0.6)
    assert flagged.flags == ["low_confidence:unit_price"]


def test_order_total_must_match_line_sum():
    order = Order(total=Decimal("700"), lines=[line("1", "650", "650"), line("1", "50", "50")])
    assert validate_order(order).flags == []
    order = Order(total=Decimal("710"), lines=order.lines)
    assert validate_order(order).flags == ["lines_total_mismatch"]


def test_untaxed_total_preferred_over_total_for_line_sum():
    order = Order(untaxed_total=Decimal("650"), total=Decimal("728"), lines=[line("1", "650", "650")])
    assert validate_order(order).flags == []
