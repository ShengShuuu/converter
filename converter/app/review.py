"""Per-field review state (attention tiers, live cross-checks) and the export preview. Pure: no I/O."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from converter.app.field_input import (
    DATETIME_PLACEHOLDER,
    HEADER_FIELDS,
    LINE_FIELDS,
    FieldKind,
    field_kind,
    format_field_value,
)
from converter.app.flag_messages import ALWAYS_VERIFY_MESSAGE, describe_flag, split_flag
from converter.extraction.validate import amounts_disagree, validate_order
from converter.mapping import CANONICAL_FIELDS, flatten_orders, literal_text
from converter.models import BBox, Order
from converter.profiles import LayoutProfile

Tier = Literal["not_read", "check", "verify", "checked", "ok"]
ATTENTION_TIERS: frozenset[str] = frozenset({"not_read", "check", "verify"})
NOT_READ_KINDS = frozenset({"not_found", "unparsed", "column_not_found"})
# Stored at extraction time but stale after any edit, so they are recomputed live instead.
LIVE_CHECK_FLAGS = frozenset({"amount_mismatch", "lines_total_mismatch"})
AMOUNT_FIELDS = ("quantity", "unit_price", "subtotal")
PREVIEW_DATETIME_FORMAT = "%Y-%m-%d %H:%M"
NO_LINE_ITEMS_MESSAGE = "No item rows were found on this order, so the download will have no products in it."
# Order-level flags meaning item rows may be missing or wrong; no field cell carries them, so export must ask.
ITEM_TABLE_FLAGS_WITH_LINES = ("table_end_not_found", "orphan_table_text")


@dataclass(frozen=True)
class FieldCell:
    key: str
    field: str
    line_index: int | None
    label: str
    value: str | None
    tier: Tier
    messages: list[str]
    bbox: BBox | None
    kind: FieldKind
    raw_text: str | None = None
    placeholder: str | None = None
    error: str | None = None

    @property
    def needs_attention(self) -> bool:
        return self.tier in ATTENTION_TIERS


@dataclass(frozen=True)
class LineRow:
    index: int
    cells: dict[str, FieldCell]
    messages: list[str]


@dataclass(frozen=True)
class ReviewSummary:
    unchecked_count: int
    field_count: int
    messages: list[str]


@dataclass(frozen=True)
class TotalsCheck:
    lines_sum: str | None
    total: str | None
    agrees: bool
    # untaxed_total when present (Odoo prints it whenever taxes apply), otherwise total.
    compared_field: str


@dataclass(frozen=True)
class ReviewView:
    order_cells: dict[str, FieldCell]
    line_rows: list[LineRow]
    summary: ReviewSummary
    totals: TotalsCheck
    # Plain-language reasons the item rows may be missing or wrong; see item_table_warnings().
    item_table_warnings: list[str]

    def cell(self, key: str) -> FieldCell:
        line_index, field = parse_field_key(key)
        return self.order_cells[field] if line_index is None else self.line_rows[line_index].cells[field]


def line_field_key(line_index: int, field: str) -> str:
    return f"line.{line_index}.{field}"


def parse_field_key(key: str) -> tuple[int | None, str]:
    """"total" -> (None, "total"); "line.2.subtotal" -> (2, "subtotal"). Raises ValueError for anything else."""
    if key in HEADER_FIELDS:
        return None, key
    prefix, _, rest = key.partition(".")
    index_text, _, field = rest.partition(".")
    if prefix != "line" or not index_text.isdigit() or field not in LINE_FIELDS:
        raise ValueError(f"not a review field key: {key!r}")
    return int(index_text), field


def remap_confirmed_after_line_delete(confirmed_fields: set[str], deleted_index: int) -> set[str]:
    """Drop the deleted line's keys and shift later lines' keys down by one, so confirmations stay on their line."""
    remapped = set()
    for key in confirmed_fields:
        line_index, field = parse_field_key(key)
        if line_index is None or line_index < deleted_index:
            remapped.add(key)
        elif line_index > deleted_index:
            remapped.add(line_field_key(line_index - 1, field))
    return remapped


def field_label(field: str, line_index: int | None) -> str:
    return CANONICAL_FIELDS[f"order.{field}" if line_index is None else f"line.{field}"]


def lines_total_disagrees(order: Order) -> bool:
    return "lines_total_mismatch" in validate_order(order.model_copy(update={"flags": []})).flags


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def item_table_warnings(order: Order) -> list[str]:
    """Why the item rows may be missing or wrong; non-empty means export needs acknowledgement when lines are mapped."""
    if not order.lines:
        reasons = [describe_flag("table_header_not_found")] if "table_header_not_found" in order.flags else []
        return [NO_LINE_ITEMS_MESSAGE, *reasons]
    # table_header_not_found with lines present means the user added them by hand, so it no longer applies.
    return [describe_flag(flag) for flag in ITEM_TABLE_FLAGS_WITH_LINES if flag in order.flags]


def _build_cell(
    key: str,
    field: str,
    line_index: int | None,
    value,
    flags: list[str],
    confirmed_fields: frozenset[str] | set[str],
    profile: LayoutProfile,
    bbox: BBox | None,
) -> FieldCell:
    if key in confirmed_fields:
        tier: Tier = "checked"
        messages: list[str] = []
    elif flags:
        tier = "not_read" if any(split_flag(flag)[0] in NOT_READ_KINDS for flag in flags) else "check"
        messages = _unique([describe_flag(flag) for flag in flags])
    elif field in profile.always_verify_fields:
        tier = "verify"
        messages = [ALWAYS_VERIFY_MESSAGE]
    else:
        tier = "ok"
        messages = []
    return FieldCell(
        key=key,
        field=field,
        line_index=line_index,
        label=field_label(field, line_index),
        value=format_field_value(value),
        tier=tier,
        messages=messages,
        bbox=bbox,
        kind=field_kind(field),
        placeholder=DATETIME_PLACEHOLDER if field_kind(field) == "datetime" else None,
    )


def build_review(order: Order, confirmed_fields: frozenset[str] | set[str], profile: LayoutProfile) -> ReviewView:
    header_flags: dict[str, list[str]] = {field: [] for field in HEADER_FIELDS}
    column_flags: dict[str, list[str]] = {field: [] for field in LINE_FIELDS}
    summary_messages: list[str] = []
    for flag in order.flags:
        if flag in LIVE_CHECK_FLAGS:
            continue
        _, field = split_flag(flag)
        if field in header_flags:
            header_flags[field].append(flag)
        elif field in column_flags:
            # Order-level flag about a table column (e.g. column_not_found:taxes) applies to every line's cell.
            column_flags[field].append(flag)
            summary_messages.append(f"{field_label(field, 0)}: {describe_flag(flag)}")
        else:
            summary_messages.append(describe_flag(flag))

    compared_field = "untaxed_total" if order.untaxed_total is not None else "total"
    totals_disagree = lines_total_disagrees(order)
    if totals_disagree:
        header_flags[compared_field].append("lines_total_mismatch")
        summary_messages.append(describe_flag("lines_total_mismatch"))

    order_cells = {
        field: _build_cell(field, field, None, getattr(order, field), flags, confirmed_fields, profile, None)
        for field, flags in header_flags.items()
    }

    line_rows = []
    for index, line in enumerate(order.lines):
        cell_flags: dict[str, list[str]] = {field: list(column_flags[field]) for field in LINE_FIELDS}
        line_messages = []
        for flag in line.flags:
            if flag in LIVE_CHECK_FLAGS:
                continue
            _, field = split_flag(flag)
            if field in cell_flags:
                cell_flags[field].append(flag)
            else:
                line_messages.append(describe_flag(flag))
        if amounts_disagree(line.quantity, line.unit_price, line.subtotal):
            for field in AMOUNT_FIELDS:
                cell_flags[field].append("amount_mismatch")
            line_messages.append(describe_flag("amount_mismatch"))
        cells = {
            field: _build_cell(
                line_field_key(index, field), field, index, getattr(line, field), flags,
                confirmed_fields, profile, line.bbox,
            )
            for field, flags in cell_flags.items()
        }
        line_rows.append(LineRow(index, cells, line_messages))

    all_cells = list(order_cells.values()) + [cell for row in line_rows for cell in row.cells.values()]
    subtotals = [line.subtotal for line in order.lines]
    lines_sum = sum(subtotals, Decimal(0)) if subtotals and None not in subtotals else None
    expected = getattr(order, compared_field)
    if not order.lines:
        summary_messages.insert(0, NO_LINE_ITEMS_MESSAGE)
    return ReviewView(
        order_cells=order_cells,
        line_rows=line_rows,
        summary=ReviewSummary(
            unchecked_count=sum(cell.needs_attention for cell in all_cells),
            field_count=len(all_cells),
            messages=_unique(summary_messages),
        ),
        item_table_warnings=item_table_warnings(order),
        totals=TotalsCheck(
            lines_sum=format_field_value(lines_sum),
            total=format_field_value(expected),
            agrees=lines_sum is not None and expected is not None and not totals_disagree,
            compared_field=compared_field,
        ),
    )


# --- export preview ---


@dataclass(frozen=True)
class PreviewCell:
    value: str | None
    tier: Tier


@dataclass(frozen=True)
class Preview:
    columns: list[str]
    rows: list[list[PreviewCell]]
    # Distinct fields needing attention that actually reach the export (unmapped fields do not count).
    unchecked_count: int
    # review.item_table_warnings when the template maps any line.* column, else empty.
    item_table_warnings: list[str]

    @property
    def needs_acknowledgement(self) -> bool:
        return bool(self.unchecked_count or self.item_table_warnings)

    def acknowledgement_reason(self) -> str:
        """Why export is blocked until acknowledged; only meaningful when needs_acknowledgement."""
        parts = list(self.item_table_warnings)
        if self.unchecked_count:
            parts.append(f"{self.unchecked_count} field(s) in this download have not been checked.")
        action = "Fix this in review" if self.item_table_warnings else "Check them"
        return " ".join([*parts, f"{action}, or tick the box to confirm you want to download anyway."])


def preview_value(value) -> str | None:
    if isinstance(value, datetime):
        return value.strftime(PREVIEW_DATETIME_FORMAT)
    return format_field_value(value)


def build_preview(order: Order, review: ReviewView, headers: list[str], column_map: dict[str, str]) -> Preview:
    value_rows = flatten_orders([order], headers, column_map)
    rows = []
    unchecked_keys = set()
    # flatten_orders emits one row per line, or a single row when there are no lines.
    for line_index, values in enumerate(value_rows):
        row = []
        for header, value in zip(headers, values):
            field_path = column_map.get(header)
            cell = None
            # if field_path is not None:
            # A literal column is fixed template text, not an extracted field, so it has nothing to review.
            if field_path is not None and literal_text(field_path) is None:
                source, field = field_path.split(".", 1)
                if source == "order":
                    cell = review.order_cells[field]
                elif line_index < len(review.line_rows):
                    cell = review.line_rows[line_index].cells[field]
            tier: Tier = cell.tier if cell is not None else "ok"
            if cell is not None and cell.needs_attention:
                unchecked_keys.add(cell.key)
            row.append(PreviewCell(preview_value(value), tier))
        rows.append(row)
    # return Preview(columns=list(headers), rows=rows, unchecked_count=len(unchecked_keys))
    maps_line_fields = any(field_path.startswith("line.") for field_path in column_map.values())
    return Preview(
        columns=list(headers),
        rows=rows,
        unchecked_count=len(unchecked_keys),
        item_table_warnings=review.item_table_warnings if maps_line_fields else [],
    )


# --- batch export preview (many orders, one per page) ---


@dataclass(frozen=True)
class OrderAttention:
    """One order whose exported fields or item rows still need a look before download."""

    page_number: int
    order_ref: str | None
    unchecked_count: int
    item_table_warnings: list[str]

    @property
    def label(self) -> str:
        return f"Page {self.page_number} ({self.order_ref})" if self.order_ref else f"Page {self.page_number}"

    def describe(self) -> str:
        parts = list(self.item_table_warnings)
        if self.unchecked_count:
            parts.append(f"{self.unchecked_count} field(s) have not been checked.")
        return f"{self.label}: {' '.join(parts)}"


@dataclass(frozen=True)
class BatchPreview:
    columns: list[str]
    rows: list[list[PreviewCell]]
    # Page each row came from; shown in the preview only, never exported.
    row_pages: list[int]
    order_count: int
    orders_needing_attention: list[OrderAttention]
    # Pages that could not be read: their orders are simply missing from the export.
    failed_pages: list[int]
    # not_sales_order or skipped by the user; informational only, never blocks the export.
    skipped_pages: list[int]
    # order_ref -> pages it was extracted from, for refs seen on more than one page.
    duplicate_order_refs: dict[str, list[int]]

    @property
    def unchecked_count(self) -> int:
        # Summed per order: the same field unchecked on two orders is two things to check, not one.
        return sum(attention.unchecked_count for attention in self.orders_needing_attention)

    @property
    def item_table_warnings(self) -> list[str]:
        """Every order's item-row warnings, each prefixed with its page label."""
        return [
            f"{attention.label}: {warning}"
            for attention in self.orders_needing_attention
            for warning in attention.item_table_warnings
        ]

    @property
    def needs_acknowledgement(self) -> bool:
        return bool(self.orders_needing_attention or self.failed_pages or self.duplicate_order_refs)

    def acknowledgement_reason(self) -> str:
        """Why export is blocked until acknowledged; only meaningful when needs_acknowledgement."""
        parts = [attention.describe() for attention in self.orders_needing_attention]
        if self.failed_pages:
            # parts.append(f"{_pages_text(self.failed_pages)} could not be read, so their orders are not in this download.")
            orders_are = "its order is" if len(self.failed_pages) == 1 else "their orders are"
            parts.append(f"{pages_text(self.failed_pages)} could not be read, so {orders_are} not in this download.")
        for order_ref, pages in self.duplicate_order_refs.items():
            parts.append(
                f"Order {order_ref} was read from more than one page ({pages_text(pages).lower()}), "
                "so its rows would appear more than once."
            )
        return " ".join([*parts, "Fix these in review, or tick the box to confirm you want to download anyway."])


def pages_text(page_numbers: list[int]) -> str:
    noun = "Page" if len(page_numbers) == 1 else "Pages"
    return f"{noun} {', '.join(str(number) for number in page_numbers)}"


def _duplicate_order_refs(entries: list[tuple[int, Order, ReviewView]]) -> dict[str, list[int]]:
    pages_by_ref: dict[str, list[int]] = {}
    display_ref: dict[str, str] = {}
    for page_number, order, _ in entries:
        if not order.order_ref or not order.order_ref.strip():
            continue
        # Case/whitespace-insensitive: OCR and hand edits can differ only in those.
        key = order.order_ref.strip().casefold()
        display_ref.setdefault(key, order.order_ref.strip())
        pages_by_ref.setdefault(key, []).append(page_number)
    return {display_ref[key]: pages for key, pages in pages_by_ref.items() if len(pages) > 1}


def build_batch_preview(
    entries: list[tuple[int, Order, ReviewView]],
    headers: list[str],
    column_map: dict[str, str],
    failed_pages: list[int],
    skipped_pages: list[int],
) -> BatchPreview:
    """Concatenate build_preview per order (page order) and collect what the batch export gate must ask about."""
    entries = sorted(entries, key=lambda entry: entry[0])
    rows: list[list[PreviewCell]] = []
    row_pages: list[int] = []
    attention: list[OrderAttention] = []
    for page_number, order, review in entries:
        preview = build_preview(order, review, headers, column_map)
        rows.extend(preview.rows)
        row_pages.extend([page_number] * len(preview.rows))
        if preview.needs_acknowledgement:
            attention.append(
                OrderAttention(page_number, order.order_ref, preview.unchecked_count, preview.item_table_warnings)
            )
    return BatchPreview(
        columns=list(headers),
        rows=rows,
        row_pages=row_pages,
        order_count=len(entries),
        orders_needing_attention=attention,
        failed_pages=sorted(failed_pages),
        skipped_pages=sorted(skipped_pages),
        duplicate_order_refs=_duplicate_order_refs(entries),
    )
