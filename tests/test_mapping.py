from openpyxl import load_workbook

from converter.app.display import is_money_path
from converter.export.xlsx import read_template_headers
from converter.mapping import (
    CANONICAL_FIELDS,
    LITERAL_PREFIX,
    ODOO_SALE_ORDER_COLUMNS,
    flatten_orders,
    literal_text,
    suggest_column_map,
)
from converter.models import Order, OrderLine


def test_real_sample_template_headers_map_exactly(template_path):
    sheet_name, headers = read_template_headers(load_workbook(template_path))
    assert sheet_name == "Converted"
    assert suggest_column_map(headers) == ODOO_SALE_ORDER_COLUMNS


def test_fuzzy_and_alias_headers_are_suggested():
    suggestion = suggest_column_map(["Qty", "Order Line/Unit Price", "Sub-Total", "customer name", "SO No."])
    assert suggestion == {
        "Qty": "line.quantity",
        "Order Line/Unit Price": "line.unit_price",
        "Sub-Total": "line.subtotal",
        "customer name": "order.customer",
        "SO No.": "order.order_ref",
    }


def test_unrelated_headers_are_left_unmapped():
    assert suggest_column_map(["Invoice Number", "Remarks", "Warehouse", ""]) == {}


def test_each_field_is_suggested_for_one_header_only():
    assert suggest_column_map(["Quantity", "Qty", "Order Lines/Quantity"]) == {"Quantity": "line.quantity"}


def test_exact_matches_win_over_earlier_fuzzy_candidates():
    # "Totl" would fuzzy-match Total, but the exact "Grand Total" header claims it first.
    assert suggest_column_map(["Totl", "Grand Total"]) == {"Grand Total": "order.total"}


def test_suggestions_only_use_canonical_field_paths():
    assert set(suggest_column_map(list(ODOO_SALE_ORDER_COLUMNS)).values()) <= set(CANONICAL_FIELDS)


def test_no_field_path_collides_with_the_literal_prefix():
    paths = set(CANONICAL_FIELDS) | set(ODOO_SALE_ORDER_COLUMNS.values())
    assert not any(path.startswith(LITERAL_PREFIX) for path in paths)


def test_literal_column_repeats_its_text_on_every_row():
    orders = [
        Order(customer="ACME", lines=[OrderLine(product="A"), OrderLine(product="B")]),
        Order(customer="Other"),
    ]
    headers = ["Customer", "Status", "Product", "Note"]
    # A dot in the text must not be read as a field path.
    column_map = {"Customer": "order.customer", "Status": "literal:line.unit_price", "Product": "line.product"}
    assert flatten_orders(orders, headers, column_map) == [
        ["ACME", "line.unit_price", "A", None],
        ["ACME", "line.unit_price", "B", None],
        ["Other", "line.unit_price", None, None],
    ]


def test_literal_text_only_for_prefixed_values():
    assert literal_text("literal:Pending") == "Pending"
    assert literal_text("order.customer") is None and literal_text(None) is None
    assert not is_money_path("literal:x.unit_price") and is_money_path("line.unit_price")
