"""Line-item table: header detection, column boundaries, and row grouping with wrapped-line merging."""

from dataclasses import dataclass, field
from statistics import median

from converter.extraction.anchors import find_label_in_line
from converter.extraction.normalize import has_digit
from converter.extraction.words import OcrWord
from converter.profiles import ColumnSpec


@dataclass(frozen=True)
class HeaderCell:
    spec: ColumnSpec
    left: int
    right: int
    height: int


@dataclass(frozen=True)
class ColumnSpan:
    field: str
    left_bound: float
    right_bound: float


@dataclass
class TableRow:
    cells: dict[str, list[OcrWord]] = field(default_factory=dict)
    words: list[OcrWord] = field(default_factory=list)


def find_table_header(
    lines: list[list[OcrWord]], columns: list[ColumnSpec], threshold: float
) -> tuple[int, list[HeaderCell]] | None:
    for line_index, line in enumerate(lines):
        cells = []
        for spec in columns:
            span = find_label_in_line(line, spec.label, threshold)
            if span:
                words = line[span[0] : span[1]]
                cells.append(HeaderCell(spec, words[0].left, words[-1].right, max(w.height for w in words)))
        found = {cell.spec.field for cell in cells}
        if all(spec.field in found for spec in columns if spec.required):
            return line_index, sorted(cells, key=lambda c: c.left)
    return None


def column_spans(header_cells: list[HeaderCell], pad_ratio: float) -> list[ColumnSpan]:
    pad = pad_ratio * median(cell.height for cell in header_cells)
    boundaries = []
    for current, following in zip(header_cells, header_cells[1:]):
        # Right-aligned values end at their header's right edge but may overflow its left edge.
        if current.spec.align == "right":
            boundaries.append(current.right + pad)
        else:
            boundaries.append(following.left - pad)
    lefts = [float("-inf"), *boundaries]
    rights = [*boundaries, float("inf")]
    return [ColumnSpan(cell.spec.field, left, right) for cell, left, right in zip(header_cells, lefts, rights)]


def column_of(word: OcrWord, spans: list[ColumnSpan]) -> str | None:
    for span in spans:
        if span.left_bound <= word.center_x < span.right_bound:
            return span.field
    return None


def assign_to_columns(line: list[OcrWord], spans: list[ColumnSpan]) -> dict[str, list[OcrWord]]:
    cells: dict[str, list[OcrWord]] = {}
    for word in line:
        for span in spans:
            if span.left_bound <= word.center_x < span.right_bound:
                cells.setdefault(span.field, []).append(word)
                break
    return cells


def group_rows(
    lines: list[list[OcrWord]], spans: list[ColumnSpan], row_start_fields: set[str]
) -> tuple[list[TableRow], list[list[OcrWord]]]:
    """Group table lines into item rows; lines without a digit in a row-start column are wrapped continuations."""
    rows: list[TableRow] = []
    orphan_lines: list[list[OcrWord]] = []
    for line in lines:
        cells = assign_to_columns(line, spans)
        # if row_start_fields & cells.keys():
        # A lone border pipe, pen tick, or glyph fragment in a row-start column must not open a phantom item.
        if any(has_digit(word.text) for name in row_start_fields & cells.keys() for word in cells[name]):
            rows.append(TableRow())
        elif not rows:
            orphan_lines.append(line)
            continue
        row = rows[-1]
        for field_name, words in cells.items():
            row.cells.setdefault(field_name, []).extend(words)
        row.words.extend(line)
    return rows, orphan_lines
