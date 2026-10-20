"""Plain-language text for extraction flags (`<kind>:<field>` or a bare order/line-level kind)."""

FIELD_FLAG_MESSAGES: dict[str, str] = {
    "not_found": "Not found on the scan.",
    "unparsed": "Text was read here but could not be understood as a valid value.",
    "column_not_found": "This column's heading was not found on the form, so its values may be missing or in the wrong column.",
    "low_confidence": "The scan was hard to read here.",
    "ambiguous": "More than one possible value was found.",
    "unexpected_format": "The value does not look like the expected format.",
    "extra_text": "Extra text was found next to the value.",
    "source_pending": "Where this field comes from on the form is not confirmed yet.",
}

STANDALONE_FLAG_MESSAGES: dict[str, str] = {
    "table_header_not_found": "The item table's heading row was not found, so no line items could be read.",
    "table_end_not_found": "The end of the item table was not found; text below the table may have been read as items.",
    "orphan_table_text": "Some text inside the item table could not be placed on any line item.",
    "lines_total_mismatch": "The line amounts do not add up to the total.",
    "amount_mismatch": "Quantity x unit price does not match the line amount.",
}

ALWAYS_VERIFY_MESSAGE = "Always compare this with the scan: it is often misread even when nothing else looks wrong."
UNKNOWN_FLAG_MESSAGE = "Needs checking ({flag})."


def split_flag(flag: str) -> tuple[str, str | None]:
    kind, _, field = flag.partition(":")
    return kind, field or None


def describe_flag(flag: str) -> str:
    kind, field = split_flag(flag)
    messages = FIELD_FLAG_MESSAGES if field else STANDALONE_FLAG_MESSAGES
    return messages.get(kind, UNKNOWN_FLAG_MESSAGE.format(flag=flag))
