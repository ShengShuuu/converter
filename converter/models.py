"""Canonical Order/OrderLine models shared by extraction, review, store and export."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class BBox(BaseModel):
    page: int | None = None
    left: int | None = None
    top: int | None = None
    width: int | None = None
    height: int | None = None


class OrderLine(BaseModel):
    product: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    subtotal: Decimal | None = None
    taxes: str | None = None
    # Per-field OCR confidence in 0..1, keyed by field name; absent key means no text was read.
    confidence: dict[str, float] = Field(default_factory=dict)
    bbox: BBox | None = None
    flags: list[str] = Field(default_factory=list)


class Order(BaseModel):
    id: int | None = None
    order_ref: str | None = None
    order_date: datetime | None = None
    customer: str | None = None
    salesperson: str | None = None
    payment_terms: str | None = None
    untaxed_total: Decimal | None = None
    total: Decimal | None = None
    source_pages: list[int] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    lines: list[OrderLine] = Field(default_factory=list)
