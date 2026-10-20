"""Cross-checks that turn extracted values into review flags; blanks are valid, not errors."""

from collections.abc import Collection
from decimal import Decimal

from converter.models import Order, OrderLine

AMOUNT_TOLERANCE = Decimal("0.01")
# Printed on every genuine item, unlike subtotal/taxes, so a blank one marks a phantom or split row.
ALWAYS_PRINTED_LINE_FIELDS = ("quantity", "unit_price")


def amounts_disagree(quantity: Decimal | None, unit_price: Decimal | None, subtotal: Decimal | None) -> bool:
    if quantity is None or unit_price is None or subtotal is None:
        return False
    return abs(quantity * unit_price - subtotal) > AMOUNT_TOLERANCE


# def validate_line(line: OrderLine, low_confidence_threshold: float) -> OrderLine:
def validate_line(line: OrderLine, low_confidence_threshold: float, unmatched_columns: Collection[str] = ()) -> OrderLine:
    flags = list(line.flags)
    flags += [f"low_confidence:{name}" for name, conf in line.confidence.items() if conf < low_confidence_threshold]
    if amounts_disagree(line.quantity, line.unit_price, line.subtotal):
        flags.append("amount_mismatch")
    # A stray mark OCR'd as a digit opens a description-less item; the totals cross-check misses it when it reads as 0.
    if line.product is None:
        flags.append("not_found:product")
    # Skip fields already flagged unparsed (text was there) or whose column header was never matched (order-level flag).
    flags += [
        f"not_found:{name}"
        for name in ALWAYS_PRINTED_LINE_FIELDS
        if getattr(line, name) is None and f"unparsed:{name}" not in flags and name not in unmatched_columns
    ]
    return line.model_copy(update={"flags": flags})


def validate_order(order: Order) -> Order:
    flags = list(order.flags)
    subtotals = [line.subtotal for line in order.lines]
    # Odoo prints an Untaxed Amount row whenever taxes apply, so Total equals the line sum only without it.
    expected_sum = order.untaxed_total if order.untaxed_total is not None else order.total
    if expected_sum is not None and subtotals and None not in subtotals:
        if abs(sum(subtotals, Decimal(0)) - expected_sum) > AMOUNT_TOLERANCE:
            flags.append("lines_total_mismatch")
    return order.model_copy(update={"flags": flags})
