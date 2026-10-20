"""Template-header to canonical-field mapping and order flattening (one row per line item)."""

from rapidfuzz import fuzz, process, utils

from converter.models import Order, OrderLine

# Below this, a header is left unmapped rather than guessed.
SUGGESTION_SCORE_CUTOFF = 85

# Field paths are strings, not callables, so a user-edited mapping can be stored as JSON later.
ODOO_SALE_ORDER_COLUMNS: dict[str, str] = {
    "Order Reference": "order.order_ref",
    "Creation Date": "order.order_date",
    "Customer": "order.customer",
    "Salesperson": "order.salesperson",
    "Payment Terms": "order.payment_terms",
    "Order Lines/Product": "line.product",
    "Order Lines/Quantity": "line.quantity",
    "Order Lines/Subtotal": "line.subtotal",
    "Order Lines/Taxes": "line.taxes",
}

CANONICAL_FIELDS: dict[str, str] = {
    "order.order_ref": "Order Reference",
    "order.order_date": "Order Date",
    "order.customer": "Customer",
    "order.salesperson": "Salesperson",
    "order.payment_terms": "Payment Terms",
    "order.untaxed_total": "Untaxed Amount",
    "order.total": "Total",
    "line.product": "Product",
    "line.quantity": "Quantity",
    "line.unit_price": "Unit Price",
    "line.subtotal": "Subtotal",
    "line.taxes": "Taxes",
}

# column_map value "literal:<text>" writes <text> into every row; field paths never start with this.
LITERAL_PREFIX = "literal:"

FIELD_ALIASES: dict[str, list[str]] = {
    "order.order_ref": ["Order Ref", "Order #", "Order No.", "Order Number", "SO No.", "SO Number", "SO #", "Reference"],
    "order.order_date": ["Creation Date", "Date", "Date Ordered", "SO Date"],
    "order.customer": ["Customer Name", "Client", "Partner", "Sold To", "Bill To"],
    "order.salesperson": ["Sales Person", "Sales Rep", "Sales Agent", "Agent"],
    "order.payment_terms": ["Mode of Payment", "Payment Method", "Payment Mode", "Terms"],
    "order.untaxed_total": ["Untaxed Total", "Amount Before Tax", "Net Amount", "VATable Sales"],
    "order.total": ["Grand Total", "Total Amount", "Amount Due", "Order Total"],
    "line.product": ["Order Lines/Product", "Description", "Item", "Item Description", "Particulars"],
    "line.quantity": ["Order Lines/Quantity", "Qty", "Qty."],
    "line.unit_price": ["Order Lines/Unit Price", "Price", "Unit Cost", "Price Unit"],
    "line.subtotal": ["Order Lines/Subtotal", "Amount", "Line Total", "Line Amount"],
    "line.taxes": ["Order Lines/Taxes", "Tax", "VAT"],
}


def _header_variants() -> list[tuple[str, str]]:
    """(header text, field path) pairs from the Odoo column names, display labels and aliases."""
    variants = list(ODOO_SALE_ORDER_COLUMNS.items())
    variants += [(label, field_path) for field_path, label in CANONICAL_FIELDS.items()]
    variants += [(alias, field_path) for field_path, aliases in FIELD_ALIASES.items() for alias in aliases]
    return variants


def suggest_column_map(headers: list[str]) -> dict[str, str]:
    """Suggest header -> field path; exact (case-insensitive) matches first, then fuzzy. Each field used once."""
    variants = _header_variants()
    exact = {text.casefold(): field_path for text, field_path in reversed(variants)}
    suggestion: dict[str, str] = {}
    for header in headers:
        field_path = exact.get(header.strip().casefold())
        if field_path and field_path not in suggestion.values() and header not in suggestion:
            suggestion[header] = field_path

    candidates = []
    for header in headers:
        if header in suggestion:
            continue
        unused = [(text, field_path) for text, field_path in variants if field_path not in suggestion.values()]
        # token_sort_ratio, not WRatio: WRatio's partial matching scores "Total" inside "Subtotal" as a match.
        best = process.extractOne(
            header,
            [text for text, _ in unused],
            scorer=fuzz.token_sort_ratio,
            processor=utils.default_process,
            score_cutoff=SUGGESTION_SCORE_CUTOFF,
        )
        if best is not None:
            _, score, index = best
            candidates.append((score, header, unused[index][1]))
    for _, header, field_path in sorted(candidates, key=lambda candidate: -candidate[0]):
        if field_path not in suggestion.values():
            suggestion[header] = field_path
    return suggestion


def resolve_field(order: Order, line: OrderLine | None, field_path: str):
    source_name, attribute = field_path.split(".", 1)
    source = order if source_name == "order" else line
    return getattr(source, attribute) if source is not None else None


def literal_text(mapping_value: str | None) -> str | None:
    """The fixed text of a "literal:<text>" column_map value; None for a field path or an unmapped column."""
    if mapping_value is not None and mapping_value.startswith(LITERAL_PREFIX):
        return mapping_value[len(LITERAL_PREFIX) :]
    return None


def _column_value(order: Order, line: OrderLine | None, mapping_value: str | None):
    if mapping_value is None:
        return None
    text = literal_text(mapping_value)
    return text if text is not None else resolve_field(order, line, mapping_value)


def flatten_orders(orders: list[Order], headers: list[str], column_map: dict[str, str]) -> list[list]:
    """One output row per line item, order header fields repeated; headers without a mapping stay blank."""
    rows = []
    for order in orders:
        # An order with no extracted lines still gets a row so it is not silently dropped from the export.
        for line in order.lines or [None]:
            # rows.append(
            #     [resolve_field(order, line, column_map[header]) if header in column_map else None for header in headers]
            # )
            rows.append([_column_value(order, line, column_map.get(header)) for header in headers])
    return rows
