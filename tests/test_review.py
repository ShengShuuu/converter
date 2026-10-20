"""Review state: attention tiers per flag kind, always-verify, live cross-checks, confirmation remap, preview."""

from datetime import datetime
from decimal import Decimal

import pytest

from converter.app.flag_messages import ALWAYS_VERIFY_MESSAGE, describe_flag
from converter.app.review import (
    NO_LINE_ITEMS_MESSAGE,
    build_batch_preview,
    build_preview,
    build_review,
    parse_field_key,
    remap_confirmed_after_line_delete,
)
from converter.mapping import ODOO_SALE_ORDER_COLUMNS
from converter.models import BBox, Order, OrderLine


def line(quantity="1", unit_price="10", subtotal="10", **extra) -> OrderLine:
    return OrderLine(
        product=extra.pop("product", "ITEM"),
        quantity=Decimal(quantity) if quantity else None,
        unit_price=Decimal(unit_price) if unit_price else None,
        subtotal=Decimal(subtotal) if subtotal else None,
        **extra,
    )


def test_tier_follows_flag_kind(profile):
    order = Order(
        order_ref="S1",
        customer="ACME",
        salesperson="MARK",
        flags=["not_found:payment_terms", "unparsed:order_date", "low_confidence:customer", "ambiguous:salesperson"],
    )
    cells = build_review(order, set(), profile).order_cells
    assert cells["payment_terms"].tier == "not_read"
    assert cells["order_date"].tier == "not_read"
    assert cells["customer"].tier == "check"
    assert cells["salesperson"].tier == "check"
    assert cells["order_ref"].tier == "ok" and cells["order_ref"].messages == []
    assert cells["customer"].messages == [describe_flag("low_confidence:customer")]


def test_unflagged_always_verify_fields_need_verifying(profile):
    order = Order(order_date=datetime(2026, 4, 22), total=Decimal("10"), lines=[line()])
    review = build_review(order, set(), profile)
    assert review.order_cells["order_date"].tier == "verify"
    assert review.order_cells["order_date"].messages == [ALWAYS_VERIFY_MESSAGE]
    assert review.order_cells["total"].tier == "verify"
    assert review.line_rows[0].cells["subtotal"].tier == "verify"
    assert review.line_rows[0].cells["unit_price"].tier == "verify"
    assert review.line_rows[0].cells["quantity"].tier == "ok"


def test_confirmed_fields_are_checked_even_when_flagged(profile):
    order = Order(flags=["not_found:total"], lines=[line(flags=["low_confidence:product"])])
    review = build_review(order, {"total", "line.0.product"}, profile)
    assert review.order_cells["total"].tier == "checked"
    assert review.order_cells["total"].messages == []
    assert review.line_rows[0].cells["product"].tier == "checked"


def test_line_cells_carry_line_bbox_and_keys(profile):
    bbox = BBox(page=1, left=1, top=2, width=3, height=4)
    review = build_review(Order(lines=[line(), line(bbox=bbox)]), set(), profile)
    cell = review.line_rows[1].cells["subtotal"]
    assert (cell.key, cell.line_index, cell.field, cell.bbox) == ("line.1.subtotal", 1, "subtotal", bbox)
    assert (cell.label, cell.kind, cell.value) == ("Subtotal", "money", "10")
    assert review.cell("line.1.subtotal") == cell
    assert review.order_cells["order_date"].placeholder is not None


def test_column_not_found_marks_every_line_and_the_summary(profile):
    order = Order(flags=["column_not_found:taxes", "table_end_not_found"], lines=[line(), line()])
    review = build_review(order, set(), profile)
    assert [row.cells["taxes"].tier for row in review.line_rows] == ["not_read", "not_read"]
    assert review.summary.messages == [
        "Taxes: " + describe_flag("column_not_found:taxes"),
        describe_flag("table_end_not_found"),
    ]


def test_amount_mismatch_is_recomputed_from_current_values(profile):
    mismatched = Order(lines=[line("2", "10", "25")])
    row = build_review(mismatched, set(), profile).line_rows[0]
    assert {row.cells[name].tier for name in ("quantity", "unit_price", "subtotal")} == {"check"}
    assert row.messages == [describe_flag("amount_mismatch")]

    # Stored flag from extraction is stale after the user fixed the subtotal.
    edited = Order(lines=[line("2", "10", "20", flags=["amount_mismatch"])])
    row = build_review(edited, {"line.0.subtotal"}, profile).line_rows[0]
    assert row.messages == []
    assert row.cells["quantity"].tier == "ok"


def test_lines_total_check_updates_after_an_edit(profile):
    order = Order(total=Decimal("40"), lines=[line("1", "10", "10"), line("1", "20", "20")])
    review = build_review(order, set(), profile)
    assert review.totals.lines_sum == "30" and review.totals.total == "40"
    assert review.totals.agrees is False and review.totals.compared_field == "total"
    assert review.order_cells["total"].tier == "check"
    assert describe_flag("lines_total_mismatch") in review.summary.messages

    edited = order.model_copy(update={"total": Decimal("30"), "flags": ["lines_total_mismatch"]})
    review = build_review(edited, set(), profile)
    assert review.totals.agrees is True
    assert review.order_cells["total"].tier == "verify"
    assert describe_flag("lines_total_mismatch") not in review.summary.messages


def test_lines_total_compares_untaxed_amount_when_present(profile):
    order = Order(untaxed_total=Decimal("30"), total=Decimal("33.60"), lines=[line("1", "30", "30")])
    totals = build_review(order, set(), profile).totals
    assert (totals.compared_field, totals.total, totals.agrees) == ("untaxed_total", "30", True)


def test_lines_sum_is_blank_when_a_line_amount_is_missing(profile):
    order = Order(total=Decimal("10"), lines=[line(), line(subtotal=None)])
    totals = build_review(order, set(), profile).totals
    assert totals.lines_sum is None and totals.agrees is False


def test_summary_counts_fields_needing_attention(profile):
    order = Order(order_ref="S1", flags=["not_found:customer"], lines=[line()])
    summary = build_review(order, {"order_date"}, profile).summary
    # 7 header + 5 line cells; customer (not_read), total, untaxed_total, line unit_price/subtotal (verify).
    assert (summary.field_count, summary.unchecked_count) == (12, 5)


def test_confirmed_keys_follow_their_line_after_delete():
    confirmed = {"total", "line.0.product", "line.1.subtotal", "line.3.subtotal", "line.12.taxes"}
    assert remap_confirmed_after_line_delete(confirmed, 1) == {
        "total", "line.0.product", "line.2.subtotal", "line.11.taxes",
    }


@pytest.mark.parametrize("key", ["", "line", "line.x.subtotal", "line.1.colour", "order.total", "line.-1.taxes"])
def test_invalid_field_keys_are_rejected(key):
    with pytest.raises(ValueError):
        parse_field_key(key)


def test_preview_marks_cells_and_counts_only_mapped_fields(profile):
    order = Order(
        order_ref="S1",
        order_date=datetime(2026, 4, 22, 10, 41),
        flags=["not_found:customer"],
        lines=[line(), line()],
    )
    review = build_review(order, set(), profile)
    headers = ["Order Reference", "Creation Date", "Order Lines/Subtotal", "Unmapped"]
    column_map = {header: ODOO_SALE_ORDER_COLUMNS[header] for header in headers[:3]}

    preview = build_preview(order, review, headers, column_map)

    assert preview.columns == headers
    assert [[cell.value for cell in row] for row in preview.rows] == [
        ["S1", "2026-04-22 10:41", "10", None],
        ["S1", "2026-04-22 10:41", "10", None],
    ]
    assert [cell.tier for cell in preview.rows[0]] == ["ok", "verify", "verify", "ok"]
    # order_date + two line subtotals; the unmapped customer flag does not count.
    assert preview.unchecked_count == 3


def test_no_line_items_needs_acknowledgement_when_lines_are_mapped(profile):
    order = Order(order_ref="S1", flags=["table_header_not_found"])
    review = build_review(order, {"order_ref"}, profile)
    expected = [NO_LINE_ITEMS_MESSAGE, describe_flag("table_header_not_found")]
    assert review.item_table_warnings == expected
    assert review.summary.messages[0] == NO_LINE_ITEMS_MESSAGE

    headers = ["Order Reference", "Order Lines/Product"]
    column_map = {header: ODOO_SALE_ORDER_COLUMNS[header] for header in headers}
    lines_mapped = build_preview(order, review, headers, column_map)
    assert lines_mapped.unchecked_count == 0 and lines_mapped.item_table_warnings == expected
    assert lines_mapped.needs_acknowledgement
    assert lines_mapped.acknowledgement_reason().startswith("No item rows were found")
    assert "have not been checked" not in lines_mapped.acknowledgement_reason()

    header_only = build_preview(order, review, headers, {"Order Reference": "order.order_ref"})
    assert header_only.item_table_warnings == [] and not header_only.needs_acknowledgement


def test_item_table_flags_need_acknowledgement_only_while_they_apply(profile):
    # Lines added by hand after table_header_not_found: that flag no longer applies.
    added_by_hand = Order(flags=["table_header_not_found"], lines=[line()])
    assert build_review(added_by_hand, set(), profile).item_table_warnings == []

    suspect = Order(flags=["table_end_not_found", "orphan_table_text"], lines=[line()])
    assert build_review(suspect, set(), profile).item_table_warnings == [
        describe_flag("table_end_not_found"),
        describe_flag("orphan_table_text"),
    ]


# --- batch preview ---

BATCH_HEADERS = list(ODOO_SALE_ORDER_COLUMNS)
BATCH_MAP = dict(ODOO_SALE_ORDER_COLUMNS)


def all_keys(review) -> set[str]:
    cells = list(review.order_cells.values()) + [cell for row in review.line_rows for cell in row.cells.values()]
    return {cell.key for cell in cells}


def entry(profile, page_number, order_ref, *, lines=2, confirm_all=True):
    order = Order(order_ref=order_ref, customer="ACME", total=Decimal("20"), lines=[line() for _ in range(lines)])
    review = build_review(order, set(), profile)
    if confirm_all:
        review = build_review(order, all_keys(review), profile)
    return page_number, order, review


def test_batch_preview_concatenates_orders_in_page_order(profile):
    entries = [entry(profile, 5, "S2", lines=1), entry(profile, 2, "S1", lines=2)]

    preview = build_batch_preview(entries, BATCH_HEADERS, BATCH_MAP, failed_pages=[], skipped_pages=[3, 1])

    assert preview.row_pages == [2, 2, 5]
    assert [row[0].value for row in preview.rows] == ["S1", "S1", "S2"]
    assert preview.order_count == 2 and preview.columns == BATCH_HEADERS
    assert preview.skipped_pages == [1, 3]
    assert not preview.needs_acknowledgement and preview.unchecked_count == 0


def test_batch_unchecked_count_is_summed_per_order_not_unioned(profile):
    single = build_preview(*entry(profile, 1, "S1", confirm_all=False)[1:], BATCH_HEADERS, BATCH_MAP)
    entries = [entry(profile, 1, "S1", confirm_all=False), entry(profile, 4, "S4", confirm_all=False)]

    preview = build_batch_preview(entries, BATCH_HEADERS, BATCH_MAP, failed_pages=[], skipped_pages=[])

    assert single.unchecked_count > 0
    assert preview.unchecked_count == 2 * single.unchecked_count
    assert [(a.page_number, a.order_ref, a.unchecked_count) for a in preview.orders_needing_attention] == [
        (1, "S1", single.unchecked_count),
        (4, "S4", single.unchecked_count),
    ]
    reason = preview.acknowledgement_reason()
    assert f"Page 1 (S1): {single.unchecked_count} field(s) have not been checked." in reason
    assert f"Page 4 (S4): {single.unchecked_count} field(s) have not been checked." in reason


def test_batch_only_orders_needing_attention_are_listed(profile):
    entries = [entry(profile, 1, "S1"), entry(profile, 2, None, lines=0)]

    preview = build_batch_preview(entries, BATCH_HEADERS, BATCH_MAP, failed_pages=[], skipped_pages=[])

    (attention,) = preview.orders_needing_attention
    assert (attention.page_number, attention.label, attention.unchecked_count) == (2, "Page 2", 0)
    assert attention.item_table_warnings == [NO_LINE_ITEMS_MESSAGE]
    assert preview.acknowledgement_reason().startswith(f"Page 2: {NO_LINE_ITEMS_MESSAGE}")


def test_failed_pages_need_acknowledgement_but_skipped_pages_do_not(profile):
    skipped_only = build_batch_preview([entry(profile, 1, "S1")], BATCH_HEADERS, BATCH_MAP, [], [2, 3])
    assert not skipped_only.needs_acknowledgement

    with_failed = build_batch_preview([entry(profile, 1, "S1")], BATCH_HEADERS, BATCH_MAP, [4, 2], [3])

    assert with_failed.needs_acknowledgement and with_failed.failed_pages == [2, 4]
    assert "Pages 2, 4 could not be read, so their orders are not in this download." in with_failed.acknowledgement_reason()
    assert "3" not in with_failed.acknowledgement_reason().replace("2, 4", "")


def test_duplicate_order_refs_need_acknowledgement(profile):
    entries = [entry(profile, 1, "S08824"), entry(profile, 2, "S9"), entry(profile, 6, " s08824 ")]

    preview = build_batch_preview(entries, BATCH_HEADERS, BATCH_MAP, failed_pages=[], skipped_pages=[])

    assert preview.duplicate_order_refs == {"S08824": [1, 6]}
    assert preview.needs_acknowledgement and preview.orders_needing_attention == []
    assert "Order S08824 was read from more than one page (pages 1, 6)" in preview.acknowledgement_reason()


def test_orders_without_order_ref_are_not_duplicates(profile):
    entries = [entry(profile, 1, None), entry(profile, 2, None), entry(profile, 3, "  ")]
    preview = build_batch_preview(entries, BATCH_HEADERS, BATCH_MAP, failed_pages=[], skipped_pages=[])
    assert preview.duplicate_order_refs == {}


def test_batch_item_table_warnings_are_labelled_by_page(profile):
    preview = build_batch_preview([entry(profile, 2, "S1", lines=0)], BATCH_HEADERS, BATCH_MAP, [], [])
    assert preview.item_table_warnings == [f"Page 2 (S1): {NO_LINE_ITEMS_MESSAGE}"]
