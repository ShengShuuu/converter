"""End-to-end extraction against a SYNTHETIC clean TSV page.

Proves the extraction code is correct on a clean case; it does not measure real OCR accuracy
against stamp/handwriting noise, which needs the user's real scanned file.
"""

import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from converter.extraction import OcrWord, extract_order
from converter.extraction.classify import select_profile
from converter.profiles import PROFILES_DIR, LayoutProfile


def test_tsv_parser_keeps_only_word_rows(synthetic_words):
    assert all(word.text for word in synthetic_words)
    assert synthetic_words[0].text == "ACME"
    assert len(synthetic_words) == 58


def test_header_fields(synthetic_order):
    assert synthetic_order.order_ref == "S08824"
    assert synthetic_order.order_date == datetime(2026, 4, 22, 10, 41, 30)
    assert synthetic_order.salesperson == "MARK LUISEN"
    assert synthetic_order.source_pages == [1]


# Replaced: customer and payment_terms now have confirmed anchors instead of being source_pending.
# def test_unconfirmed_fields_stay_blank_and_flagged(synthetic_order):
#     assert synthetic_order.customer is None
#     assert synthetic_order.payment_terms is None
#     assert "source_pending:customer" in synthetic_order.flags
#     assert "source_pending:payment_terms" in synthetic_order.flags


def test_customer_and_payment_terms_extracted(synthetic_order):
    assert synthetic_order.customer == "ACME TRADING CORP."
    assert synthetic_order.payment_terms == "POS / THERMAL PAYMENT"
    assert not any(flag.startswith("source_pending:") for flag in synthetic_order.flags)
    assert not any(flag.endswith((":customer", ":payment_terms")) for flag in synthetic_order.flags)


def test_customer_skips_letterhead_and_keeps_contact_as_printed(letterhead_words, profile):
    order = extract_order(letterhead_words, profile)
    assert order.customer == "DON JOSE HEIGHTS HOA, INC, ROSE L. DE TORRES"
    assert order.payment_terms == "GCASH PAYMENT"
    assert order.order_ref == "S07002"
    assert order.salesperson == "JOACHAS SANTOS"
    assert [line.subtotal for line in order.lines] == [Decimal("1500.00")]
    assert order.flags == []


# Replaced: it encoded "address line silently becomes the customer" as expected behavior.
# def test_customer_block_ends_at_order_line(synthetic_words, profile):
#     order = extract_order(_without(synthetic_words, "ACME", "TRADING", "CORP."), profile)
#     assert order.customer == "Quezon City, Philippines"


def test_lone_address_line_without_name_is_flagged_ambiguous(synthetic_words, profile):
    order = extract_order(_without(synthetic_words, "ACME", "TRADING", "CORP."), profile)
    assert order.customer == "Quezon City, Philippines"
    assert "ambiguous:customer" in order.flags


ADDRESS_LINE_WORDS = ("Quezon", "City,", "Philippines")


def _shifted_down(words, texts, offset):
    return [replace(w, top=w.top + offset) if w.text in texts else w for w in words]


# Stamps print crisply, so low_confidence:customer cannot catch them; geometry must.
@pytest.mark.parametrize(
    ("words_factory", "expected_customer"),
    [
        pytest.param(
            lambda w: _add_word(w, "RECEIVED", 1500, 450, 200, height=40, conf=97.0),
            "RECEIVED",
            id="stamp_between_block_and_order_line",
        ),
        pytest.param(
            lambda w: _add_word(w, "RECEIVED", 150, 450, 200, height=40, conf=97.0),
            "RECEIVED",
            id="stamp_in_other_column_above_order_line",
        ),
        pytest.param(
            lambda w: _add_word(w, "RECEIVED", 1750, 390, 200, height=40, conf=97.0),
            "ACME TRADING CORP.",
            id="stamp_tucked_under_block_not_left_aligned",
        ),
        pytest.param(
            lambda w: _add_word(w, "BRIGHTLINE", 1500, 240, 190, height=32),
            "BRIGHTLINE",
            id="letterhead_just_under_gap_threshold",
        ),
        pytest.param(
            lambda w: _shifted_down(w, ADDRESS_LINE_WORDS, 21),
            "Quezon City, Philippines",
            id="block_fragmented_just_over_gap_threshold",
        ),
        pytest.param(
            lambda w: _add_word(_add_word(w, "Invoice", 150, 300, 130), "Copy", 150, 345, 90),
            "Invoice",
            id="two_equally_tall_blocks",
        ),
    ],
)
def test_shaky_customer_block_is_flagged_ambiguous(synthetic_words, profile, words_factory, expected_customer):
    order = extract_order(words_factory(synthetic_words), profile)
    assert order.customer == expected_customer
    assert "ambiguous:customer" in order.flags
    assert order.order_ref == "S08824"


@pytest.mark.parametrize(
    "words_factory",
    [
        pytest.param(lambda w: _add_word(w, "Invoice", 150, 300, 130), id="same_height_as_name_line"),
        pytest.param(lambda w: _add_word(w, "RECEIVED", 150, 345, 200), id="same_height_as_address_line"),
    ],
)
def test_other_column_text_at_block_height_is_not_merged_into_customer(synthetic_words, profile, words_factory):
    order = extract_order(words_factory(synthetic_words), profile)
    assert order.customer == "ACME TRADING CORP."
    assert not any(flag.endswith(":customer") for flag in order.flags)


def _add_left_column_block(words, *texts):
    """Stack lines in the left column so the last one sits on the address row, directly above Order #."""
    last_top = 345
    return [
        *words,
        *(
            OcrWord(text=text, left=150, top=last_top - 45 * (len(texts) - 1 - row), width=180, height=30, conf=93.0)
            for row, text in enumerate(texts)
        ),
    ]


# A separate shipping/invoicing address block can print in the left column, beside the real customer block.
@pytest.mark.parametrize(
    ("block_texts", "expected_customer"),
    [
        pytest.param(("Shipping", "Warehouse", "Pasig", "Metro"), "Shipping", id="four_line_block_beats_customer"),
        pytest.param(("Invoicing", "BRIGHTLINE", "Pasig"), "Invoicing", id="three_line_block_beats_customer"),
    ],
)
def test_taller_block_in_other_column_is_flagged_ambiguous(synthetic_words, profile, block_texts, expected_customer):
    order = extract_order(_add_left_column_block(synthetic_words, *block_texts), profile)
    assert order.customer == expected_customer
    assert "ambiguous:customer" in order.flags


def _add_top_aligned_left_column_block(words, *texts):
    """Stack lines in the left column starting on the customer name row, so a longer block ends lower."""
    first_top = 300
    return [
        *words,
        *(
            OcrWord(text=text, left=150, top=first_top + 45 * row, width=180, height=30, conf=93.0)
            for row, text in enumerate(texts)
        ),
    ]


# The real customer block ends above the row directly over Order #, so it is never seeded as a candidate.
@pytest.mark.parametrize(
    ("block_texts", "expected_customer"),
    [
        pytest.param(("Shipping", "Warehouse", "Pasig", "Metro"), "Shipping", id="four_line_block_ends_below_customer"),
        pytest.param(("Invoicing", "BRIGHTLINE", "Pasig"), "Invoicing", id="three_line_block_ends_below_customer"),
    ],
)
def test_top_aligned_longer_block_in_other_column_is_flagged_ambiguous(
    synthetic_words, profile, block_texts, expected_customer
):
    order = extract_order(_add_top_aligned_left_column_block(synthetic_words, *block_texts), profile)
    assert order.customer == expected_customer
    assert "ambiguous:customer" in order.flags
    assert order.order_ref == "S08824"


ADDRESS_BLOCK_WORDS = ("ACME", "TRADING", "CORP.", "Quezon", "City,", "Philippines")
PAYMENT_ROW_TOP = 1290


def test_no_text_above_order_line_flags_customer_not_found(synthetic_words, profile):
    order = extract_order(_without(synthetic_words, *ADDRESS_BLOCK_WORDS), profile)
    assert order.customer is None
    assert "not_found:customer" in order.flags
    assert order.order_ref == "S08824"


def test_missing_order_anchor_flags_customer_not_found(synthetic_words, profile):
    order = extract_order(_without(synthetic_words, "#", "S08824"), profile)
    assert order.customer is None
    assert {"not_found:order_ref", "not_found:customer"} <= set(order.flags)


def test_low_confidence_customer_is_flagged(synthetic_words, profile):
    words = [replace(word, conf=40.0) if word.text == "TRADING" else word for word in synthetic_words]
    order = extract_order(words, profile)
    assert order.customer == "ACME TRADING CORP."
    assert "low_confidence:customer" in order.flags


@pytest.mark.parametrize(
    "removed_words",
    [
        pytest.param(("Mode", "of", "Payment:", "POS", "/", "THERMAL", "PAYMENT"), id="no_payment_line"),
        pytest.param(("POS", "/", "THERMAL", "PAYMENT"), id="label_without_value"),
    ],
)
def test_missing_payment_terms_is_flagged_not_found(synthetic_words, profile, removed_words):
    # "/" also appears in product descriptions, so removal is limited to the payment row.
    words = [w for w in synthetic_words if not (w.text in removed_words and w.top >= PAYMENT_ROW_TOP)]
    order = extract_order(words, profile)
    assert order.payment_terms is None
    assert "not_found:payment_terms" in order.flags
    _assert_both_items_kept(order)


def test_line_items_separated_and_wrapped_line_merged(synthetic_order):
    first, second = synthetic_order.lines
    assert first.product == "FSHB100 / 100W F-SERIES CONVENTIONAL HIGHBAY 24 MONTHS WARRANTY"
    assert first.quantity == Decimal("1.00")
    assert first.unit_price == Decimal("2600.00")
    assert first.subtotal == Decimal("2600.00")
    assert first.taxes is None
    assert second.product == "PX-S100 / 100W PX-S SOLAR FLOOD LIGHT"
    assert second.quantity == Decimal("2.00")
    assert second.unit_price == Decimal("2880.00")
    assert second.subtotal == Decimal("5760.00")
    assert second.taxes == "Output Tax (12%)"


def test_bbox_spans_wrapped_continuation(synthetic_order):
    bbox = synthetic_order.lines[0].bbox
    assert (bbox.page, bbox.left, bbox.top) == (1, 150, 960)
    assert bbox.top + bbox.height == 1034


def test_totals_and_table_end(synthetic_order):
    assert synthetic_order.total == Decimal("8360.00")
    assert synthetic_order.untaxed_total is None
    assert "lines_total_mismatch" not in synthetic_order.flags
    assert "table_end_not_found" not in synthetic_order.flags


def test_money_fields_are_decimal_never_float(synthetic_order):
    assert isinstance(synthetic_order.total, Decimal)
    for line in synthetic_order.lines:
        for value in (line.quantity, line.unit_price, line.subtotal):
            assert isinstance(value, Decimal)


def test_confidence_and_flags(synthetic_order):
    first, second = synthetic_order.lines
    assert first.flags == []
    assert set(first.confidence) == {"product", "quantity", "unit_price", "subtotal"}
    assert first.confidence["product"] == 0.88
    assert second.confidence["unit_price"] == 0.52
    assert second.flags == ["low_confidence:unit_price"]
    assert not any(flag.startswith(("low_confidence:order", "not_found:")) for flag in synthetic_order.flags)


def _with_text(words, old, new, occurrence=0):
    matches = [i for i, word in enumerate(words) if word.text == old]
    index = matches[occurrence]
    return [replace(word, text=new) if i == index else word for i, word in enumerate(words)]


def test_amount_mismatch_is_flagged(synthetic_words, profile):
    order = extract_order(_with_text(synthetic_words, "5,760.00", "5,670.00"), profile)
    assert "amount_mismatch" in order.lines[1].flags
    assert "lines_total_mismatch" in order.flags


def test_unreadable_amount_is_blank_and_flagged(synthetic_words, profile):
    order = extract_order(_with_text(synthetic_words, "2,600.00", "2,6O0.00", occurrence=1), profile)
    assert order.lines[0].subtotal is None
    assert "unparsed:subtotal" in order.lines[0].flags


def _without(words, *texts):
    return [word for word in words if word.text not in texts]


def _add_word(words, text, left, top, width, height=30, conf=90.0):
    return [*words, OcrWord(text=text, left=left, top=top, width=width, height=height, conf=conf)]


# Replaced: matching "/" by text also moved the product-description slashes.
# def _moved_to_row(words, texts, top):
#     return [replace(word, top=top) if word.text in texts else word for word in words]


def _moved_to_row(words, from_top, to_top):
    return [replace(word, top=to_top) if word.top >= from_top else word for word in words]


TOTAL_ROW_TOP = 1190
# MODE_OF_PAYMENT_WORDS = {"Mode", "of", "Payment:", "POS", "/", "THERMAL", "PAYMENT"}
EXPECTED_PRODUCTS = [
    "FSHB100 / 100W F-SERIES CONVENTIONAL HIGHBAY 24 MONTHS WARRANTY",
    "PX-S100 / 100W PX-S SOLAR FLOOD LIGHT",
]


def _assert_both_items_kept(order):
    assert [line.subtotal for line in order.lines] == [Decimal("2600.00"), Decimal("5760.00")]


# Bug #1: a label-like word inside an item row must never be taken as the end of the table.
@pytest.mark.parametrize("product_code", ["TOTAL", "Total", "TOTAL:", "T0TAL", "TOTAL-X"])
def test_total_like_product_code_does_not_truncate_table(synthetic_words, profile, product_code):
    order = extract_order(_with_text(synthetic_words, "PX-S100", product_code), profile)
    _assert_both_items_kept(order)
    assert order.lines[1].product.startswith(product_code)
    assert order.total == Decimal("8360.00")
    assert "table_end_not_found" not in order.flags
    assert "lines_total_mismatch" not in order.flags


def test_total_like_wrapped_description_line_stays_in_its_item(synthetic_words, profile):
    order = extract_order(_with_text(synthetic_words, "24", "TOTAL"), profile)
    _assert_both_items_kept(order)
    assert order.lines[0].product.endswith("TOTAL MONTHS WARRANTY")
    assert order.total == Decimal("8360.00")


def test_missing_totals_row_keeps_all_items_and_flags(synthetic_words, profile):
    words = _with_text(_without(synthetic_words, "Total", "₱8,360.00"), "PX-S100", "TOTAL")
    order = extract_order(words, profile)
    _assert_both_items_kept(order)
    assert order.total is None
    assert "table_end_not_found" in order.flags


# Bug #4: a found totals row whose value cannot be read must be flagged, not silently blank.
@pytest.mark.parametrize("misread_total", ["₱8,36O.00", "8.360.00", "₱8,360.O0", "B,360.00"])
def test_unparseable_total_is_blank_and_flagged(synthetic_words, profile, misread_total):
    order = extract_order(_with_text(synthetic_words, "₱8,360.00", misread_total), profile)
    assert order.total is None
    assert "unparsed:total" in order.flags
    assert "table_end_not_found" not in order.flags
    _assert_both_items_kept(order)


@pytest.mark.parametrize("peso_suffix", ["₱", "P", ")"])
def test_peso_suffix_on_real_form_amounts_is_parsed(synthetic_words, profile, peso_suffix):
    words = _with_text(synthetic_words, "₱8,360.00", f"8,360.00{peso_suffix}")
    words = _with_text(words, "5,760.00", f"5,760.00{peso_suffix}")
    order = extract_order(words, profile)
    assert order.total == Decimal("8360.00")
    assert order.lines[1].subtotal == Decimal("5760.00")
    assert not any(flag.startswith("unparsed:") for flag in [*order.flags, *order.lines[1].flags])


def test_totals_row_without_value_is_flagged_not_found(synthetic_words, profile):
    order = extract_order(_without(synthetic_words, "₱8,360.00"), profile)
    assert order.total is None
    assert "not_found:total" in order.flags
    assert "table_end_not_found" not in order.flags
    _assert_both_items_kept(order)


# Bug #3: stamp/handwriting text sharing the totals row must not hide it or create a phantom item.
@pytest.mark.parametrize(
    "words_factory",
    [
        pytest.param(lambda w: _add_word(w, "Rcvd", 1000, TOTAL_ROW_TOP, 80), id="stamp_left_of_label"),
        pytest.param(lambda w: _add_word(w, "PAID", 1950, TOTAL_ROW_TOP, 90), id="stamp_between_label_and_value"),
        pytest.param(
            # lambda w: _add_word(_moved_to_row(w, MODE_OF_PAYMENT_WORDS, TOTAL_ROW_TOP), "Rcvd", 40, TOTAL_ROW_TOP, 80),
            lambda w: _add_word(_moved_to_row(w, PAYMENT_ROW_TOP, TOTAL_ROW_TOP), "Rcvd", 40, TOTAL_ROW_TOP, 80),
            id="payment_line_merged_into_total_row",
        ),
    ],
)
def test_extra_text_on_totals_row_is_flagged_not_a_phantom_item(synthetic_words, profile, words_factory):
    order = extract_order(words_factory(synthetic_words), profile)
    _assert_both_items_kept(order)
    assert [line.product for line in order.lines] == EXPECTED_PRODUCTS
    assert order.payment_terms == "POS / THERMAL PAYMENT"
    assert order.total == Decimal("8360.00")
    assert "extra_text:total" in order.flags
    assert "table_end_not_found" not in order.flags
    assert "lines_total_mismatch" not in order.flags


# Replaced: now covered by the payment_line_merged_into_total_row case above.
# def test_payment_terms_read_when_merged_into_totals_row(synthetic_words, profile):
#     # Moved by row, not text: "/" also occurs in product descriptions.
#     words = [replace(w, top=TOTAL_ROW_TOP) if w.top >= PAYMENT_ROW_TOP else w for w in synthetic_words]
#     words = _add_word(words, "Rcvd", 40, TOTAL_ROW_TOP, 80)
#     assert extract_order(words, profile).payment_terms == "POS / THERMAL PAYMENT"


@pytest.mark.parametrize(
    "positional_field",
    [
        pytest.param({"field": "customer", "anchor_field": "order_date"}, id="anchor_not_labeled"),
        pytest.param({"field": "order_ref", "anchor_field": "order_ref"}, id="field_also_labeled"),
    ],
)
def test_profile_rejects_invalid_positional_field(positional_field):
    with pytest.raises(ValueError):
        LayoutProfile(
            name="bad",
            header_fields=[{"field": "order_ref", "label": "Order #"}],
            positional_fields=[{**positional_field, "position": "first_line_of_block_above"}],
            table_columns=[{"field": "product", "label": "DESCRIPTION", "align": "left"}],
        )


CUSTOMER_SPEC = {"field": "customer", "anchor_field": "order_ref", "position": "first_line_of_block_above"}


@pytest.mark.parametrize(
    "positional_fields",
    [
        pytest.param([{**CUSTOMER_SPEC, "block_max_gap_ratio": 0}], id="zero_gap_ratio"),
        pytest.param([{**CUSTOMER_SPEC, "block_max_gap_ratio": -1.0}], id="negative_gap_ratio"),
        pytest.param([CUSTOMER_SPEC, {**CUSTOMER_SPEC, "block_max_gap_ratio": 2.0}], id="duplicate_positional_field"),
    ],
)
def test_profile_rejects_invalid_block_settings(positional_fields):
    with pytest.raises(ValueError):
        LayoutProfile(
            name="bad",
            header_fields=[{"field": "order_ref", "label": "Order #"}],
            positional_fields=positional_fields,
            table_columns=[{"field": "product", "label": "DESCRIPTION", "align": "left"}],
        )


def _profile_json_with_customer_spec(**overrides):
    data = json.loads((PROFILES_DIR / "odoo_sale_order_v1.json").read_text(encoding="utf-8"))
    data["positional_fields"][0].update(overrides)
    return json.dumps(data)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"block_max_gap_ratio": "inf"}, id="infinite_gap_ratio"),
        pytest.param({"block_max_gap_ratio": "nan"}, id="nan_gap_ratio"),
        pytest.param({"block_borderline_margin": "inf"}, id="infinite_borderline_margin"),
        pytest.param({"min_block_line": 1}, id="misspelled_key"),
    ],
)
def test_profile_json_rejects_non_finite_numbers_and_unknown_keys(overrides):
    with pytest.raises(ValidationError):
        LayoutProfile.model_validate_json(_profile_json_with_customer_spec(**overrides))


def test_unmodified_profile_json_still_loads():
    assert LayoutProfile.model_validate_json(_profile_json_with_customer_spec()).name == "odoo_sale_order_v1"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"value_max_gap_ratio": 0}, id="zero_value_gap_ratio"),
        pytest.param({"value_max_gap_ratio": -1.0}, id="negative_value_gap_ratio"),
        pytest.param({"line_tolerance_ratio": -5}, id="negative_line_tolerance"),
        pytest.param({"line_tolerance_ratio": 0}, id="zero_line_tolerance"),
        pytest.param({"column_pad_ratio": -1.0}, id="negative_column_pad"),
        pytest.param({"fuzzy_threshold": 0}, id="zero_fuzzy_threshold"),
        pytest.param({"fuzzy_threshold": 500}, id="fuzzy_threshold_over_100"),
        pytest.param({"low_confidence_threshold": 7}, id="confidence_threshold_over_1"),
        pytest.param({"low_confidence_threshold": -0.1}, id="negative_confidence_threshold"),
    ],
)
def test_profile_rejects_out_of_range_top_level_settings(overrides):
    data = json.loads((PROFILES_DIR / "odoo_sale_order_v1.json").read_text(encoding="utf-8"))
    with pytest.raises(ValidationError):
        LayoutProfile.model_validate_json(json.dumps({**data, **overrides}))


# Real page 10: the Total bar was not OCR'd, Untaxed Amount still ended the table, total came out blank unflagged.
def test_missing_required_total_row_is_flagged_not_found(vat_no_total_bar_words, profile):
    order = extract_order(vat_no_total_bar_words, profile)
    assert order.total is None
    assert "not_found:total" in order.flags
    assert order.untaxed_total == Decimal("8360.00")
    assert not any(flag.endswith(":untaxed_total") for flag in order.flags)
    assert "table_end_not_found" not in order.flags
    _assert_both_items_kept(order)


def test_absent_optional_totals_row_is_not_flagged(synthetic_order):
    assert synthetic_order.untaxed_total is None
    assert "not_found:untaxed_total" not in synthetic_order.flags


def test_totals_required_option_comes_from_profile(synthetic_words, profile):
    totals = [spec.model_copy(update={"required": True}) for spec in profile.totals]
    order = extract_order(synthetic_words, profile.model_copy(update={"totals": totals}))
    assert "not_found:untaxed_total" in order.flags


# Real page 6: "UNIT PRICE" OCR'd as one token, so unit prices landed in the taxes column with no flag.
def test_unmatched_optional_column_header_is_flagged(synthetic_words, profile):
    words = _without(_with_text(synthetic_words, "UNIT", "UNITPRICE|"), "PRICE")
    order = extract_order(words, profile)
    assert "column_not_found:unit_price" in order.flags
    assert [line.unit_price for line in order.lines] == [None, None]
    assert order.lines[0].taxes == "2,600.00"
    assert not any("not_found:unit_price" in line.flags for line in order.lines)


def test_matched_columns_have_no_column_flag(synthetic_order):
    assert not any(flag.startswith("column_not_found:") for flag in synthetic_order.flags)


NOISE_ROW_TOP = 1040


# Real page 10: border pipes, pen ticks, and peso-glyph fragments split 2 items into 6.
@pytest.mark.parametrize(
    ("noise_text", "left"),
    [
        pytest.param("|", 1500, id="border_pipe_in_quantity"),
        pytest.param("'", 1760, id="pen_tick_in_unit_price"),
        pytest.param("P", 2380, id="peso_fragment_in_amount"),
    ],
)
def test_digitless_noise_in_row_start_column_does_not_start_item(synthetic_words, profile, noise_text, left):
    order = extract_order(_add_word(synthetic_words, noise_text, left, NOISE_ROW_TOP, 12), profile)
    assert [line.product for line in order.lines] == EXPECTED_PRODUCTS
    assert [line.quantity for line in order.lines] == [Decimal("1.00"), Decimal("2.00")]
    assert order.lines[1].subtotal == Decimal("5760.00")


FIRST_LINE_CLEAN = {
    "product": EXPECTED_PRODUCTS[0],
    "quantity": Decimal("1.00"),
    "unit_price": Decimal("2600.00"),
    "subtotal": Decimal("2600.00"),
    "taxes": None,
    "confidence": {"product": 0.88, "quantity": 0.92, "unit_price": 0.933, "subtotal": 0.94},
    "bbox": {"page": 1, "left": 150, "top": 960, "width": 2250, "height": 110},
    "flags": [],
}


# Absorbed noise joins the real row's cell, so a correctly read value can be lost; this pins that trade-off.
@pytest.mark.parametrize(
    ("noise_text", "left", "first_line_changes"),
    [
        pytest.param("|", 1500, {"confidence": {**FIRST_LINE_CLEAN["confidence"], "quantity": 0.9}}, id="border_pipe"),
        pytest.param(
            "'",
            1760,
            {
                "unit_price": None,
                "confidence": {**FIRST_LINE_CLEAN["confidence"], "unit_price": 0.9},
                "flags": ["unparsed:unit_price"],
            },
            id="pen_tick_destroys_unit_price",
        ),
        # Before peso-glyph tolerance, "2,600.00P" was rejected: subtotal None plus unparsed:subtotal.
        pytest.param(
            "P", 2380, {"confidence": {**FIRST_LINE_CLEAN["confidence"], "subtotal": 0.9}}, id="peso_fragment_recovered"
        ),
    ],
)
def test_absorbed_noise_full_state_of_first_line(synthetic_words, profile, noise_text, left, first_line_changes):
    order = extract_order(_add_word(synthetic_words, noise_text, left, NOISE_ROW_TOP, 12), profile)
    assert order.lines[0].model_dump() == {**FIRST_LINE_CLEAN, **first_line_changes}


# A pen mark or "O" OCR'd as a digit still opens an item; a "0" adds nothing to the line sum, so only this flag shows it.
@pytest.mark.parametrize(
    ("noise_text", "left"),
    [
        pytest.param("0", 1500, id="zero_in_quantity"),
        pytest.param("0", 1760, id="zero_in_unit_price"),
        pytest.param("0", 2380, id="zero_in_amount"),
        pytest.param("1", 2380, id="one_in_amount"),
    ],
)
def test_digit_noise_phantom_item_is_flagged_missing_product(synthetic_words, profile, noise_text, left):
    order = extract_order(_add_word(synthetic_words, noise_text, left, NOISE_ROW_TOP, 12), profile)
    first, phantom, second = order.lines
    assert phantom.product is None
    assert "not_found:product" in phantom.flags
    assert [first.product, second.product] == EXPECTED_PRODUCTS
    assert not any("not_found:product" in line.flags for line in (first, second))


WRAPPED_LINE_TOP = 1004


# Digit noise on a wrapped description line opens a phantom that steals the text, so not_found:product misses it.
@pytest.mark.parametrize(
    ("noise_text", "left", "phantom_flags"),
    [
        pytest.param("0", 1500, ["not_found:unit_price"], id="zero_in_quantity"),
        pytest.param("0", 1760, ["not_found:quantity"], id="zero_in_unit_price"),
        pytest.param("0", 2380, ["not_found:quantity", "not_found:unit_price"], id="zero_in_amount"),
    ],
)
def test_phantom_item_absorbing_wrapped_description_is_flagged(
    synthetic_words, profile, noise_text, left, phantom_flags
):
    order = extract_order(_add_word(synthetic_words, noise_text, left, WRAPPED_LINE_TOP, 12), profile)
    first, phantom, second = order.lines
    assert first.product == "FSHB100 / 100W F-SERIES CONVENTIONAL HIGHBAY"
    assert phantom.product == "24 MONTHS WARRANTY"
    assert phantom.flags == phantom_flags
    assert first.flags == []
    assert second.flags == ["low_confidence:unit_price"]
    assert "lines_total_mismatch" not in order.flags


def test_clean_totals_row_has_no_extra_text_flag(synthetic_order):
    assert not any(flag.endswith(":total") for flag in synthetic_order.flags)


def test_classify_selects_matching_profile(synthetic_words, profile):
    unrelated = LayoutProfile(
        name="unrelated",
        header_fields=[{"field": "order_ref", "label": "Invoice No"}],
        table_columns=[{"field": "product", "label": "ITEM CODE", "align": "left"}],
    )
    assert select_profile(synthetic_words, [unrelated, profile]).name == "odoo_sale_order_v1"
    assert select_profile(synthetic_words, [unrelated]) is None
