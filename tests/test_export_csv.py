import csv
from datetime import datetime
from decimal import Decimal

from converter.export.csv import write_csv
from converter.mapping import ODOO_SALE_ORDER_COLUMNS
from converter.models import Order, OrderLine

HEADERS = list(ODOO_SALE_ORDER_COLUMNS)


def sample_order() -> Order:
    return Order(
        order_ref="S07001",
        order_date=datetime(2025, 11, 26, 9, 5, 7),
        customer="ACME, TRADING \"CORP\"",
        salesperson="ROSEMARIE CARDINAL",
        lines=[
            OrderLine(product="PX-S200 WW", quantity=Decimal("4.00"), subtotal=Decimal("13050.50")),
            OrderLine(product="Blank amounts", quantity=Decimal("1")),
        ],
    )


def test_csv_has_bom_headers_and_plain_formats(tmp_path):
    output = write_csv([sample_order()], tmp_path / "out.csv", HEADERS, ODOO_SALE_ORDER_COLUMNS)

    assert output.read_bytes().startswith(b"\xef\xbb\xbf")
    with output.open(encoding="utf-8-sig", newline="") as handle:
        header, *rows = list(csv.reader(handle))
    assert header == HEADERS
    common = ["S07001", "2025-11-26 09:05:07", 'ACME, TRADING "CORP"', "ROSEMARIE CARDINAL", ""]
    assert rows == [
        [*common, "PX-S200 WW", "4.00", "13050.50", ""],
        [*common, "Blank amounts", "1", "", ""],
    ]


def test_large_amounts_have_no_thousands_separator(tmp_path):
    order = Order(lines=[OrderLine(subtotal=Decimal("1234567.50"))])
    output = write_csv([order], tmp_path / "out.csv", ["Amount"], {"Amount": "line.subtotal"})
    assert output.read_text(encoding="utf-8-sig").splitlines() == ["Amount", "1234567.50"]


def test_unmapped_headers_stay_blank_and_order_without_lines_keeps_a_row(tmp_path):
    output = write_csv([Order(order_ref="S1")], tmp_path / "out.csv", ["Ref", "Notes"], {"Ref": "order.order_ref"})
    assert output.read_text(encoding="utf-8-sig").splitlines() == ["Ref,Notes", "S1,"]


def test_text_that_looks_like_a_formula_is_neutralized(tmp_path):
    order = Order(customer="=HYPERLINK(\"http://x\")", lines=[OrderLine(product="-5 discount", subtotal=Decimal("-5"))])
    column_map = {"Customer": "order.customer", "Product": "line.product", "Amount": "line.subtotal"}
    output = write_csv([order], tmp_path / "out.csv", list(column_map), column_map)
    with output.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[1] == ["'=HYPERLINK(\"http://x\")", "'-5 discount", "-5"]


def test_a_formula_like_header_is_also_neutralized(tmp_path):
    # Headers can be user-typed (manual template builder), so they need the same guard as data cells.
    output = write_csv([Order(order_ref="S1")], tmp_path / "out.csv", ["=cmd|'/c calc'!A1", "Ref"], {"Ref": "order.order_ref"})
    with output.open(encoding="utf-8-sig", newline="") as handle:
        header, *_ = list(csv.reader(handle))
    assert header == ["'=cmd|'/c calc'!A1", "Ref"]
