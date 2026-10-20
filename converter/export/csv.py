"""Write orders as CSV (UTF-8 with BOM so Excel detects the encoding), same row shape as write_xlsx."""

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from converter.mapping import flatten_orders
from converter.models import Order

CSV_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# Spreadsheet apps run cells starting with these as formulas; OCR'd text must never execute.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def format_csv_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.strftime(CSV_DATETIME_FORMAT)
    text = str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def write_csv(orders: list[Order], output_path: Path, headers: list[str], column_map: dict[str, str]) -> Path:
    with output_path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        # Headers can now be user-typed (manual template builder), so guard them like any other cell.
        writer.writerow([format_csv_value(header) for header in headers])
        for row in flatten_orders(orders, headers, column_map):
            writer.writerow([format_csv_value(value) for value in row])
    return output_path
