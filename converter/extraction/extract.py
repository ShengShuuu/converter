"""Turn one page of OCR words into an Order using a layout profile. Pure: no I/O, no subprocess."""

import re
from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from converter.extraction.anchors import (
    TextBlock,
    find_block_above,
    find_label,
    find_label_in_line,
    value_words_after,
)
from converter.extraction.normalize import clean_text, parse_datetime, parse_money, parse_quantity
from converter.extraction.table import ColumnSpan, TableRow, column_of, column_spans, find_table_header, group_rows
from converter.extraction.validate import validate_line, validate_order
from converter.extraction.words import OcrWord, group_into_lines
from converter.models import BBox, Order, OrderLine
from converter.profiles import HeaderFieldSpec, LayoutProfile, PositionalFieldSpec

NUMERIC_LINE_FIELDS = ("quantity", "unit_price", "subtotal")
DESCRIPTION_FIELD = "product"
TOTALS_VALUE_FIELD = "subtotal"


@dataclass(frozen=True)
class TotalsRow:
    line_index: int
    value_words: list[OcrWord]
    extra_words: list[OcrWord]


def extract_order(words: list[OcrWord], profile: LayoutProfile, page_number: int = 1) -> Order:
    lines = group_into_lines(words, profile.line_tolerance_ratio)
    flags: list[str] = []
    header_values = _extract_header_fields(lines, profile, flags)
    positional_values = _extract_positional_fields(lines, profile, flags)
    totals: dict[str, Decimal | None] = {}
    order_lines: list[OrderLine] = []

    table_header = find_table_header(lines, profile.table_columns, profile.fuzzy_threshold)
    if table_header is None:
        flags.append("table_header_not_found")
    else:
        header_index, header_cells = table_header
        # An unmatched optional header leaves no column boundary, so its values land in a neighbouring column.
        matched_columns = {cell.spec.field for cell in header_cells}
        missing_columns = [spec.field for spec in profile.table_columns if spec.field not in matched_columns]
        flags += [f"column_not_found:{field_name}" for field_name in missing_columns]
        # table_end, totals = _extract_totals(lines, profile, first_line=header_index + 1)
        spans = column_spans(header_cells, profile.column_pad_ratio)
        row_start_fields = {spec.field for spec in profile.table_columns if spec.starts_row}
        table_end, totals = _extract_totals(lines, profile, spans, row_start_fields, header_index + 1, flags)
        if table_end is None:
            flags.append("table_end_not_found")
            table_end = len(lines)
        # spans = column_spans(header_cells, profile.column_pad_ratio)
        # row_start_fields = {spec.field for spec in profile.table_columns if spec.starts_row}
        rows, orphan_lines = group_rows(lines[header_index + 1 : table_end], spans, row_start_fields)
        if orphan_lines:
            flags.append("orphan_table_text")
        # order_lines = [validate_line(_build_line(row, page_number), profile.low_confidence_threshold) for row in rows]
        order_lines = [
            validate_line(_build_line(row, page_number), profile.low_confidence_threshold, missing_columns)
            for row in rows
        ]

    flags += [f"source_pending:{field_name}" for field_name in profile.pending_fields]
    order = Order(
        **header_values,
        **positional_values,
        **totals,
        source_pages=[page_number],
        flags=flags,
        lines=order_lines,
    )
    return validate_order(order)


def _extract_header_fields(lines: list[list[OcrWord]], profile: LayoutProfile, flags: list[str]) -> dict:
    values = {}
    for spec in profile.header_fields:
        match = find_label(lines, spec.label, profile.fuzzy_threshold)
        if match is None:
            flags.append(f"not_found:{spec.field}")
            continue
        line = lines[match.line_index]
        stop_at = _next_label_start(line, match.end, spec, profile)
        words = value_words_after(line, match.end, stop_at, profile.value_max_gap_ratio)
        if not words:
            flags.append(f"not_found:{spec.field}")
            continue
        text = clean_text(" ".join(word.text for word in words))
        values[spec.field] = _typed_header_value(text, spec, flags)
        if min(word.conf for word in words) / 100 < profile.low_confidence_threshold:
            flags.append(f"low_confidence:{spec.field}")
    return values


def _extract_positional_fields(lines: list[list[OcrWord]], profile: LayoutProfile, flags: list[str]) -> dict:
    labels = {spec.field: spec.label for spec in profile.header_fields}
    values = {}
    for spec in profile.positional_fields:
        anchor = find_label(lines, labels[spec.anchor_field], profile.fuzzy_threshold)
        # block = block_above(lines, anchor.line_index, spec.block_max_gap_ratio) if anchor else []
        block = (
            find_block_above(
                lines, anchor.line_index, spec.block_max_gap_ratio, profile.value_max_gap_ratio, spec.min_block_lines
            )
            if anchor
            else None
        )
        if not block:
            flags.append(f"not_found:{spec.field}")
            continue
        if _block_is_ambiguous(block, spec):
            flags.append(f"ambiguous:{spec.field}")
        # Kept as printed: a trailing ", CONTACT NAME" is part of how the form shows the customer.
        # first_line = block[0]
        first_line = block.lines[0]
        values[spec.field] = clean_text(" ".join(word.text for word in first_line))
        if min(word.conf for word in first_line) / 100 < profile.low_confidence_threshold:
            flags.append(f"low_confidence:{spec.field}")
    return values


def _block_is_ambiguous(block: TextBlock, spec: PositionalFieldSpec) -> bool:
    """Geometry that a stamp, merged letterhead, or fragmented block produces instead of a clean address block."""
    borderline_gap = any(
        abs(ratio - spec.block_max_gap_ratio) <= spec.block_borderline_margin * spec.block_max_gap_ratio
        for ratio in [*block.gap_ratios, block.stop_gap_ratio]
        if ratio is not None
    )
    misaligned = any(
        abs(upper[0].left - lower[0].left) > spec.block_max_left_offset_ratio * median(w.height for w in lower)
        for upper, lower in zip(block.lines, block.lines[1:])
    )
    too_short = len(block.lines) < spec.min_block_lines
    return block.has_rival_block or too_short or borderline_gap or misaligned


def _next_label_start(line: list[OcrWord], after: int, current: HeaderFieldSpec, profile: LayoutProfile) -> int:
    starts = [len(line)]
    for other in profile.header_fields:
        if other is current:
            continue
        span = find_label_in_line(line, other.label, profile.fuzzy_threshold, start_at=after)
        if span:
            starts.append(span[0])
    return min(starts)


def _typed_header_value(text: str, spec: HeaderFieldSpec, flags: list[str]):
    if spec.kind == "datetime":
        parsed = parse_datetime(text, spec.formats)
        if parsed is None:
            flags.append(f"unparsed:{spec.field}")
        return parsed
    if spec.pattern and not re.fullmatch(spec.pattern, text):
        flags.append(f"unexpected_format:{spec.field}")
    return text


# def _extract_totals(
#     lines: list[list[OcrWord]], profile: LayoutProfile, first_line: int
# ) -> tuple[int | None, dict[str, Decimal | None]]:
#     """Return the index of the first totals row (end of the item table) and the parsed totals."""
#     table_end = None
#     totals: dict[str, Decimal | None] = {}
#     for spec in profile.totals:
#         match = find_line_start_label(lines, spec.label, profile.fuzzy_threshold, first_line)
#         if match is None:
#             continue
#         # Totals values sit far right of their label, so no gap cutoff applies here.
#         value_text = "".join(word.text for word in lines[match.line_index][match.end :])
#         totals[spec.field] = parse_decimal(value_text)
#         table_end = match.line_index if table_end is None else min(table_end, match.line_index)
#     return table_end, totals


def _extract_totals(
    lines: list[list[OcrWord]],
    profile: LayoutProfile,
    spans: list[ColumnSpan],
    row_start_fields: set[str],
    first_line: int,
    flags: list[str],
) -> tuple[int | None, dict[str, Decimal | None]]:
    """Return the index of the first totals row (end of the item table) and the parsed totals."""
    table_end = None
    totals: dict[str, Decimal | None] = {}
    item_only_fields = row_start_fields - {TOTALS_VALUE_FIELD}
    for spec in profile.totals:
        row = _find_totals_row(lines, spec.label, profile.fuzzy_threshold, spans, item_only_fields, first_line)
        # if row is None:
        #     continue
        if row is None:
            # An optional row is legitimately absent from layouts that never print it.
            if spec.required:
                flags.append(f"not_found:{spec.field}")
            continue
        if row.extra_words:
            flags.append(f"extra_text:{spec.field}")
        if not row.value_words:
            flags.append(f"not_found:{spec.field}")
        else:
            # totals[spec.field] = parse_decimal("".join(word.text for word in row.value_words))
            totals[spec.field] = parse_money("".join(word.text for word in row.value_words))
            if totals[spec.field] is None:
                flags.append(f"unparsed:{spec.field}")
        table_end = row.line_index if table_end is None else min(table_end, row.line_index)
    return table_end, totals


def _find_totals_row(
    lines: list[list[OcrWord]],
    label: str,
    threshold: float,
    spans: list[ColumnSpan],
    item_only_fields: set[str],
    first_line: int,
) -> TotalsRow | None:
    """First line where the label sits outside the description column and no item-only column holds text."""
    for line_index in range(first_line, len(lines)):
        line = lines[line_index]
        start_at = 0
        while label_span := find_label_in_line(line, label, threshold, start_at):
            label_start, label_end = label_span
            start_at = label_start + 1
            # A label-like word in the description column is a product name, not the totals anchor.
            if column_of(line[label_start], spans) == DESCRIPTION_FIELD:
                continue
            other_words = line[:label_start] + line[label_end:]
            if any(column_of(word, spans) in item_only_fields for word in other_words):
                continue
            after_label = line[label_end:]
            value_words = [word for word in after_label if column_of(word, spans) == TOTALS_VALUE_FIELD]
            extra_words = line[:label_start] + [w for w in after_label if column_of(w, spans) != TOTALS_VALUE_FIELD]
            return TotalsRow(line_index, value_words, extra_words)
    return None


def _build_line(row: TableRow, page_number: int) -> OrderLine:
    def cell_text(field_name: str, separator: str = " ") -> str | None:
        words = row.cells.get(field_name)
        return clean_text(separator.join(word.text for word in words)) if words else None

    values = {
        "product": cell_text("product"),
        "quantity": parse_quantity(cell_text("quantity")),
        # "unit_price": parse_decimal(cell_text("unit_price", separator="")),
        # "subtotal": parse_decimal(cell_text("subtotal", separator="")),
        "unit_price": parse_money(cell_text("unit_price", separator="")),
        "subtotal": parse_money(cell_text("subtotal", separator="")),
        "taxes": cell_text("taxes"),
    }
    flags = [f"unparsed:{name}" for name in NUMERIC_LINE_FIELDS if row.cells.get(name) and values[name] is None]
    confidence = {name: round(min(word.conf for word in words) / 100, 3) for name, words in row.cells.items()}
    return OrderLine(**values, confidence=confidence, bbox=_union_bbox(row.words, page_number), flags=flags)


def _union_bbox(words: list[OcrWord], page_number: int) -> BBox:
    left = min(word.left for word in words)
    top = min(word.top for word in words)
    return BBox(
        page=page_number,
        left=left,
        top=top,
        width=max(word.right for word in words) - left,
        height=max(word.bottom for word in words) - top,
    )
