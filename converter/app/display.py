"""Template-only display helpers. Never used for parsing, storage or export: those keep plain Decimal values."""

from decimal import Decimal, InvalidOperation

from converter.app.field_input import field_kind
from converter.mapping import literal_text

PESO_SIGN = "₱"


def peso(value) -> str:
    """'2900.00' -> '₱2,900.00'. Blank stays blank; anything that isn't a number is shown as-is."""
    if value is None or not str(value).strip():
        return ""
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except InvalidOperation:
        return str(value)
    if not amount.is_finite():
        return str(value)
    # No precision in the format spec: the stored decimal places are shown unrounded.
    return f"{'-' if amount < 0 else ''}{PESO_SIGN}{abs(amount):,f}"


def is_money_path(field_path: str | None) -> bool:
    """True for a column map path like 'line.unit_price', using the same field kinds the review inputs parse with."""
    # return bool(field_path) and field_kind(field_path.split(".", 1)[-1]) == "money"
    # A literal's text can contain a dot (e.g. "literal:x.unit_price"); it is never a money field.
    return bool(field_path) and literal_text(field_path) is None and field_kind(field_path.split(".", 1)[-1]) == "money"
