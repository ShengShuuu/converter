"""Parse one submitted review-form value into the typed value its Order/OrderLine field holds. Pure: no I/O."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, get_args

from converter.extraction.normalize import parse_decimal
from converter.profiles import HeaderField, LineField, TotalField

FieldKind = Literal["text", "money", "number", "datetime"]
FieldValue = str | Decimal | datetime | None

HEADER_FIELDS: tuple[str, ...] = get_args(HeaderField) + get_args(TotalField)
LINE_FIELDS: tuple[str, ...] = get_args(LineField)
FIELD_KINDS: dict[str, FieldKind] = {
    "order_date": "datetime",
    "untaxed_total": "money",
    "total": "money",
    "quantity": "number",
    "unit_price": "money",
    "subtotal": "money",
}
# datetime-local input formats (with "T") plus the same typed into the plain text input the review screen uses.
DATETIME_INPUT_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)
DATETIME_VALUE_FORMAT = "%Y-%m-%d %H:%M:%S"
DATETIME_PLACEHOLDER = "YYYY-MM-DD HH:MM"
NUMBER_ERROR = "Enter a number like 1234.50 (commas only as thousands separators)."
DATETIME_ERROR = "Enter a date as YYYY-MM-DD HH:MM, e.g. 2026-04-22 10:41."


@dataclass(frozen=True)
class ParsedInput:
    value: FieldValue
    error: str | None = None


def field_kind(field: str) -> FieldKind:
    return FIELD_KINDS.get(field, "text")


def parse_field_input(field: str, raw: str) -> ParsedInput:
    """Blank input is a valid empty value (every field is nullable); unparseable input returns an error."""
    text = raw.strip()
    if not text:
        return ParsedInput(None)
    kind = field_kind(field)
    if kind in ("money", "number"):
        amount = parse_decimal(text)
        return ParsedInput(amount) if amount is not None else ParsedInput(None, NUMBER_ERROR)
    if kind == "datetime":
        for date_format in DATETIME_INPUT_FORMATS:
            try:
                return ParsedInput(datetime.strptime(text, date_format))
            except ValueError:
                continue
        return ParsedInput(None, DATETIME_ERROR)
    return ParsedInput(text)


def format_field_value(value: FieldValue) -> str | None:
    """The string an edit input is pre-filled with; round-trips through parse_field_input."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.strftime(DATETIME_VALUE_FORMAT)
    return str(value)
