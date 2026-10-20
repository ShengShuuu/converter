"""Text-to-value normalization: money/quantity to Decimal, dates via explicit formats."""

import re
from datetime import datetime
from decimal import Decimal

# _CURRENCY_MARKS = re.compile(r"PHP|₱|\$", re.IGNORECASE)
# Edges only: a mark mid-number is a misread digit, and stripping it would yield a plausible wrong amount.
_CURRENCY_MARKS = re.compile(r"^\s*(PHP|₱|\$)|(PHP|₱|\$)\s*$", re.IGNORECASE)
# Comma is accepted only as a well-formed thousands separator so malformed reads are rejected, not reinterpreted.
_AMOUNT = re.compile(r"-?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?")
# Tesseract eng reads the printed peso sign as P, ?, ) or F; centavos are required so a glyph eating "." or a digit is not hidden.
_AMOUNT_WITH_PESO_MISREAD = re.compile(r"P?(?P<amount>-?(\d{1,3}(,\d{3})+|\d+)\.\d{2})[P?)F]{0,2}")


def clean_text(text: str) -> str:
    return " ".join(text.split())


def has_digit(text: str) -> bool:
    return any(char.isdigit() for char in text)


def parse_decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    candidate = _CURRENCY_MARKS.sub("", text).replace(" ", "")
    if not _AMOUNT.fullmatch(candidate):
        return None
    return Decimal(candidate.replace(",", ""))


def parse_money(text: str | None) -> Decimal | None:
    """Like parse_decimal, but tolerates a leading "P" or up to two trailing misread peso glyphs."""
    strict = parse_decimal(text)
    if strict is not None or text is None:
        return strict
    match = _AMOUNT_WITH_PESO_MISREAD.fullmatch(_CURRENCY_MARKS.sub("", text).replace(" ", ""))
    return Decimal(match["amount"].replace(",", "")) if match else None


def parse_quantity(text: str | None) -> Decimal | None:
    """Parse '1.00 Unit' style cells; the unit of measure is dropped."""
    if not text or not text.split():
        return None
    return parse_decimal(text.split()[0])


def parse_datetime(text: str | None, formats: list[str]) -> datetime | None:
    if not text:
        return None
    candidate = clean_text(text)
    for date_format in formats:
        try:
            return datetime.strptime(candidate, date_format)
        except ValueError:
            continue
    return None
