"""Conversion routes: upload, batch list and progress, per-page review autosave, template choice, preview, export.

Every handler is a plain def: they touch SQLite, the filesystem or openpyxl, which would block the event loop.
Per-page routes are keyed by page_number, which stays stable across extract-anyway/skip (a list position does not).
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from converter.app.field_input import HEADER_FIELDS, LINE_FIELDS, format_field_value, parse_field_input
from converter.app.review import (
    BatchPreview,
    ReviewView,
    build_batch_preview,
    build_review,
    line_field_key,
    pages_text,
    parse_field_key,
    remap_confirmed_after_line_delete,
)
from converter.app.web import data_dir, db_path, is_htmx, redirect, render, render_fragments
from converter.export.csv import write_csv
from converter.export.xlsx import write_xlsx
from converter.models import Order, OrderLine
from converter.profiles import load_profile
from converter.store import repositories as repo
from converter.worker.runner import (
    CLASSIFIER_MIN_RATIO,
    display_image_path,
    extract_from_saved_words,
    process_conversion,
    saved_words_path,
)

router = APIRouter()

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_TIFF_SIGNATURES = (b"II*\x00", b"MM\x00*")
UPLOAD_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".tif": _TIFF_SIGNATURES,
    ".tiff": _TIFF_SIGNATURES,
}
EXPORT_MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}
MISSING_WORDS_ERROR = "The saved OCR result for this page is missing. Click Retry to read the page again."
EXTRACTABLE_STATUSES = (repo.NOT_SALES_ORDER, repo.SKIPPED)
SKIPPED_STATUSES = (repo.NOT_SALES_ORDER, repo.SKIPPED)
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


# --- shared helpers ---


def batch_url(conversion_id: int) -> str:
    return f"/conversions/{conversion_id}"


def page_url(conversion_id: int, page_number: int) -> str:
    return f"/conversions/{conversion_id}/pages/{page_number}"


# def _done_conversion(request: Request, conversion_id: int) -> repo.Conversion:
#     conversion = repo.get_conversion(db_path(request), conversion_id)
#     if conversion.status != repo.DONE or conversion.order is None:
#         raise HTTPException(409, "This conversion has no extracted order to review yet.")
#     return conversion


def _done_page(request: Request, conversion_id: int, page_number: int) -> tuple[repo.Conversion, repo.Page]:
    """The page's conversion and the page itself; 404 if either is missing, 409 if it has no order to review."""
    conversion = repo.get_conversion(db_path(request), conversion_id)
    page = repo.get_page(db_path(request), conversion_id, page_number)
    if page.status != repo.DONE or page.order is None:
        raise HTTPException(409, "This page has no extracted order to review.")
    return conversion, page


def _review(order: Order, confirmed_fields: frozenset[str], profile_name: str | None) -> ReviewView:
    return build_review(order, confirmed_fields, load_profile(profile_name))


def _page_review(page: repo.Page) -> ReviewView:
    return _review(page.order, page.confirmed_fields, page.profile_name)


def _order_context(conversion: repo.Conversion, page: repo.Page) -> dict:
    """Context every review partial needs; order_url is the base of all per-page review/autosave URLs."""
    return {"conversion": conversion, "page": page, "order_url": page_url(conversion.id, page.page_number)}


def _oob_parts(order_context: dict, review: ReviewView) -> list[tuple[str, dict]]:
    context = {**order_context, "summary": review.summary, "totals": review.totals}
    return [("_review_summary.html", context), ("_totals_check.html", context)]


def _cell_response(request: Request, order_context: dict, review: ReviewView, key: str) -> Response:
    parts = [("_field_cell.html", {**order_context, "cell": review.cell(key)})]
    return render_fragments(request, parts + _oob_parts(order_context, review))


def _line_items_response(request: Request, order_context: dict, review: ReviewView) -> Response:
    parts = [("_line_items.html", {**order_context, "line_rows": review.line_rows})]
    return render_fragments(request, parts + _oob_parts(order_context, review))


def _check_line_index(order: Order, line_index: int | None) -> None:
    if line_index is not None and not 0 <= line_index < len(order.lines):
        # Raised inside update_order too, so a line deleted by a concurrent request rolls the edit back.
        raise HTTPException(409, "That line no longer exists. Reload the page.")


def _upload_error(request: Request, message: str) -> Response:
    if is_htmx(request):
        return render_fragments(request, [("_form_error.html", {"message": message})], status_code=422)
    conversions = repo.list_conversions(db_path(request))
    return render(request, "upload.html", {"conversions": conversions, "error": message}, status_code=422)


def _submit_batch(request: Request, conversion: repo.Conversion) -> None:
    """(Re)start the batch job; with one executor thread a second submission just runs after the current one."""
    if conversion.status in (repo.DONE, repo.FAILED):
        repo.set_status(db_path(request), conversion.id, repo.QUEUED)
    request.app.state.executor.submit(process_conversion, conversion.id, db_path(request), data_dir(request))


# --- batch list view ---


@dataclass(frozen=True)
class BatchPageRow:
    """One line of the batch list: a page's status and, when extracted, a summary of its order."""

    page_number: int
    status: str
    error: str | None
    review_url: str | None
    order_ref: str | None
    customer: str | None
    line_count: int | None
    total: str | None
    unchecked_count: int
    item_table_warnings: list[str]
    classifier_score: float | None
    can_extract_anyway: bool
    can_skip: bool
    can_retry: bool

    @property
    def needs_attention(self) -> bool:
        return bool(self.unchecked_count or self.item_table_warnings)


def _batch_page_row(conversion_id: int, page: repo.Page) -> BatchPageRow:
    order = page.order if page.status == repo.DONE else None
    review = _page_review(page) if order is not None else None
    score = page.classifier_score
    return BatchPageRow(
        page_number=page.page_number,
        status=page.status,
        error=page.error,
        review_url=page_url(conversion_id, page.page_number) if order is not None else None,
        order_ref=order.order_ref if order else None,
        customer=order.customer if order else None,
        line_count=len(order.lines) if order else None,
        total=format_field_value(order.total) if order else None,
        unchecked_count=review.summary.unchecked_count if review else 0,
        item_table_warnings=review.item_table_warnings if review else [],
        classifier_score=round(score, 2) if score is not None else None,
        can_extract_anyway=page.status in EXTRACTABLE_STATUSES,
        can_skip=page.status in repo.SKIPPABLE_STATUSES,
        can_retry=page.status == repo.FAILED,
    )


def _batch_context(request: Request, conversion: repo.Conversion) -> dict:
    progress = repo.page_summary(db_path(request), conversion.id)
    pages = [_batch_page_row(conversion.id, page) for page in repo.list_pages(db_path(request), conversion.id)]
    is_processing = conversion.status in (repo.QUEUED, repo.PROCESSING) or progress.pending > 0
    return {
        "conversion": conversion,
        "progress": progress,
        "pages": pages,
        "is_processing": is_processing,
        # "can_export": conversion.status == repo.DONE and progress.counts[repo.DONE] > 0,
        "can_export": conversion.status == repo.DONE and progress.counts[repo.DONE] > 0 and progress.pending == 0,
        "elapsed_seconds": max(0, int((datetime.now() - conversion.updated_at).total_seconds())),
        "classifier_threshold": CLASSIFIER_MIN_RATIO,
        "batch_url": batch_url(conversion.id),
        "template_url": f"{batch_url(conversion.id)}/template",
    }


def _batch_fragment(request: Request, conversion_id: int) -> Response:
    conversion = repo.get_conversion(db_path(request), conversion_id)
    return render_fragments(request, [("_batch_status.html", _batch_context(request, conversion))])


def _batch_action_response(request: Request, conversion_id: int) -> Response:
    """After a list action: the refreshed batch fragment for htmx, a redirect back to the list otherwise."""
    return _batch_fragment(request, conversion_id) if is_htmx(request) else redirect(batch_url(conversion_id))


# --- upload and processing ---


@router.get("/")
def upload_page(request: Request):
    return render(request, "upload.html", {"conversions": repo.list_conversions(db_path(request)), "error": None})


@router.post("/conversions")
def upload_conversion(request: Request, file: UploadFile | None = File(None)):
    if file is None or not file.filename:
        return _upload_error(request, "Choose a file to upload.")
    original_filename = file.filename.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(original_filename).suffix.lower()
    if suffix not in UPLOAD_SIGNATURES:
        return _upload_error(request, "Upload a PDF, PNG, JPG or TIFF file.")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return _upload_error(request, "The file is larger than 25 MB.")
    # The extension alone is user-controlled; the magic bytes say what the file really is.
    if not content.startswith(UPLOAD_SIGNATURES[suffix]):
        return _upload_error(request, f"This file is not a valid {suffix[1:].upper()} file.")

    conversion = repo.create_conversion(db_path(request), original_filename, suffix)
    source_path = data_dir(request) / conversion.source_path
    try:
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(content)
    except OSError:
        repo.delete_conversion(db_path(request), data_dir(request), conversion.id)
        raise
    request.app.state.executor.submit(process_conversion, conversion.id, db_path(request), data_dir(request))

    target = batch_url(conversion.id)
    return Response(headers={"HX-Redirect": target}) if is_htmx(request) else redirect(target)


# @router.get("/conversions/{conversion_id}")  -- v1 redirected to the single order's review once done
@router.get("/conversions/{conversion_id}")
def conversion_page(request: Request, conversion_id: int):
    conversion = repo.get_conversion(db_path(request), conversion_id)
    return render(request, "batch.html", _batch_context(request, conversion))


@router.get("/conversions/{conversion_id}/status")
def conversion_status(request: Request, conversion_id: int):
    # No HX-Redirect when done (v1 did): the user stays on the list and opens each order from there.
    return _batch_fragment(request, conversion_id)


@router.post("/conversions/{conversion_id}/retry")
def retry_conversion(request: Request, conversion_id: int):
    conversion = repo.get_conversion(db_path(request), conversion_id)
    requeued = repo.requeue_failed_pages(db_path(request), conversion_id)
    if requeued or conversion.status == repo.FAILED:
        _submit_batch(request, conversion)
    return _batch_action_response(request, conversion_id)


@router.delete("/conversions/{conversion_id}")
def delete_conversion(request: Request, conversion_id: int):
    repo.delete_conversion(db_path(request), data_dir(request), conversion_id)
    # 200 with an empty body, not 204: htmx 2 does not swap a 204, so the list row would stay.
    return Response(status_code=200)


# --- per-page actions ---


@router.get("/conversions/{conversion_id}/pages/{page_number}")
def review_page(request: Request, conversion_id: int, page_number: int):
    conversion = repo.get_conversion(db_path(request), conversion_id)
    # One read for the page and its neighbours, so navigation cannot disagree with the page shown.
    pages = repo.list_pages(db_path(request), conversion_id)
    page = next((p for p in pages if p.page_number == page_number), None)
    if page is None:
        raise repo.NotFoundError(f"conversion {conversion_id} has no page {page_number}")
    if page.status != repo.DONE or page.order is None:
        return redirect(batch_url(conversion_id))
    review = _page_review(page)
    done_numbers = [p.page_number for p in pages if p.status == repo.DONE]
    position = done_numbers.index(page_number)
    context = {
        **_order_context(conversion, page),
        "page_width": page.page_width,
        "page_height": page.page_height,
        "order_cells": review.order_cells,
        "line_rows": review.line_rows,
        "summary": review.summary,
        "totals": review.totals,
        "batch_url": batch_url(conversion_id),
        "template_url": f"{batch_url(conversion_id)}/template",
        "done_page_count": len(done_numbers),
        "done_page_position": position + 1,
        "prev_page_url": page_url(conversion_id, done_numbers[position - 1]) if position > 0 else None,
        "next_page_url": (
            page_url(conversion_id, done_numbers[position + 1]) if position + 1 < len(done_numbers) else None
        ),
    }
    return render(request, "review.html", context)


@router.get("/conversions/{conversion_id}/pages/{page_number}/page-image")
def page_image(request: Request, conversion_id: int, page_number: int):
    repo.get_page(db_path(request), conversion_id, page_number)
    image_path = display_image_path(data_dir(request), conversion_id, page_number)
    if not image_path.is_file():
        raise HTTPException(404, "No page image for this page.")
    return FileResponse(image_path, media_type="image/png")


@router.post("/conversions/{conversion_id}/pages/{page_number}/extract-anyway")
def extract_anyway(request: Request, conversion_id: int, page_number: int):
    page = repo.get_page(db_path(request), conversion_id, page_number)
    # Undoing a skip restores the stored order; re-extracting would wipe the user's edits and confirmations.
    if page.status == repo.SKIPPED and page.order is not None:
        if repo.restore_skipped_page(db_path(request), conversion_id, page_number):
            return _review_redirect(request, conversion_id, page_number)
        return _batch_action_response(request, conversion_id)
    if page.status in EXTRACTABLE_STATUSES:
        if saved_words_path(data_dir(request), conversion_id, page_number).is_file():
            extract_from_saved_words(conversion_id, page_number, db_path(request), data_dir(request))
            # target = page_url(conversion_id, page_number)
            # return Response(headers={"HX-Redirect": target}) if is_htmx(request) else redirect(target)
            return _review_redirect(request, conversion_id, page_number)
        repo.set_page_status(db_path(request), conversion_id, page_number, repo.FAILED, error=MISSING_WORDS_ERROR)
    return _batch_action_response(request, conversion_id)


def _review_redirect(request: Request, conversion_id: int, page_number: int) -> Response:
    target = page_url(conversion_id, page_number)
    return Response(headers={"HX-Redirect": target}) if is_htmx(request) else redirect(target)


@router.post("/conversions/{conversion_id}/pages/{page_number}/skip")
def skip_page(request: Request, conversion_id: int, page_number: int):
    page = repo.get_page(db_path(request), conversion_id, page_number)
    # Already skipped is fine (double click).
    if page.status != repo.SKIPPED and not repo.mark_page_skipped(db_path(request), conversion_id, page_number):
        # raise HTTPException(409, "This page can't be skipped while it is being read.")
        # Re-read: the status may have changed between the read above and the conditional update.
        current_status = repo.get_page(db_path(request), conversion_id, page_number).status
        if current_status == repo.NOT_SALES_ORDER:
            raise HTTPException(409, "This page was classified as not a Sales Order, so it is already left out.")
        if current_status != repo.SKIPPED:
            raise HTTPException(409, "This page is still being read. Try again once it has finished.")
    return _batch_action_response(request, conversion_id)


@router.post("/conversions/{conversion_id}/pages/{page_number}/retry")
def retry_page(request: Request, conversion_id: int, page_number: int):
    conversion = repo.get_conversion(db_path(request), conversion_id)
    repo.get_page(db_path(request), conversion_id, page_number)
    if repo.requeue_failed_pages(db_path(request), conversion_id, page_number):
        _submit_batch(request, conversion)
    return _batch_action_response(request, conversion_id)


# --- review autosave (per page) ---


def _save_field(
    request: Request, conversion_id: int, page_number: int, line_index: int | None, field: str, raw_value: str
):
    conversion, page = _done_page(request, conversion_id, page_number)
    order_context = _order_context(conversion, page)
    _check_line_index(page.order, line_index)
    key = field if line_index is None else line_field_key(line_index, field)
    parsed = parse_field_input(field, raw_value)
    if parsed.error:
        current = _page_review(page).cell(key)
        # Keep what the user typed (unsaved) so they can fix it instead of retyping.
        cell = replace(current, value=raw_value, error=parsed.error, messages=[parsed.error, *current.messages])
        return render_fragments(request, [("_field_cell.html", {**order_context, "cell": cell})], 422)

    def apply_edit(order: Order, confirmed_fields: set[str]) -> None:
        _check_line_index(order, line_index)
        setattr(order if line_index is None else order.lines[line_index], field, parsed.value)
        confirmed_fields.add(key)

    order, confirmed_fields = repo.update_order(db_path(request), conversion_id, page_number, apply_edit)
    return _cell_response(request, order_context, _review(order, confirmed_fields, page.profile_name), key)


@router.patch("/conversions/{conversion_id}/pages/{page_number}/order/{field}")
def patch_order_field(request: Request, conversion_id: int, page_number: int, field: str, value: str = Form("")):
    if field not in HEADER_FIELDS:
        raise HTTPException(404, f"Unknown order field {field!r}.")
    return _save_field(request, conversion_id, page_number, None, field, value)


@router.patch("/conversions/{conversion_id}/pages/{page_number}/lines/{line_index}/{field}")
def patch_line_field(
    request: Request, conversion_id: int, page_number: int, line_index: int, field: str, value: str = Form("")
):
    if field not in LINE_FIELDS:
        raise HTTPException(404, f"Unknown line field {field!r}.")
    return _save_field(request, conversion_id, page_number, line_index, field, value)


@router.post("/conversions/{conversion_id}/pages/{page_number}/lines")
def add_line(request: Request, conversion_id: int, page_number: int):
    conversion, page = _done_page(request, conversion_id, page_number)
    order, confirmed_fields = repo.update_order(
        db_path(request), conversion_id, page_number, lambda order, confirmed_fields: order.lines.append(OrderLine())
    )
    review = _review(order, confirmed_fields, page.profile_name)
    return _line_items_response(request, _order_context(conversion, page), review)


@router.delete("/conversions/{conversion_id}/pages/{page_number}/lines/{line_index}")
def delete_line(request: Request, conversion_id: int, page_number: int, line_index: int):
    conversion, page = _done_page(request, conversion_id, page_number)

    def remove_line(order: Order, confirmed_fields: set[str]) -> None:
        _check_line_index(order, line_index)
        del order.lines[line_index]
        remapped = remap_confirmed_after_line_delete(confirmed_fields, line_index)
        confirmed_fields.clear()
        confirmed_fields.update(remapped)

    order, confirmed_fields = repo.update_order(db_path(request), conversion_id, page_number, remove_line)
    review = _review(order, confirmed_fields, page.profile_name)
    return _line_items_response(request, _order_context(conversion, page), review)


@router.post("/conversions/{conversion_id}/pages/{page_number}/confirm/{field_key}")
def confirm_field(request: Request, conversion_id: int, page_number: int, field_key: str):
    conversion, page = _done_page(request, conversion_id, page_number)
    try:
        line_index, _ = parse_field_key(field_key)
    except ValueError:
        raise HTTPException(404, f"Unknown field {field_key!r}.") from None

    def mark_confirmed(order: Order, confirmed_fields: set[str]) -> None:
        _check_line_index(order, line_index)
        confirmed_fields.add(field_key)

    order, confirmed_fields = repo.update_order(db_path(request), conversion_id, page_number, mark_confirmed)
    review = _review(order, confirmed_fields, page.profile_name)
    return _cell_response(request, _order_context(conversion, page), review, field_key)


# --- template choice, preview, export (one per batch) ---


def _exportable_pages(request: Request, conversion: repo.Conversion) -> list[repo.Page] | None:
    """All of the batch's pages, or None while it is still being read or has no extracted order to export.

    Callers derive both the export gate and the exported orders from this one read, so they cannot disagree.
    """
    if conversion.status != repo.DONE:
        return None
    pages = repo.list_pages(db_path(request), conversion.id)
    return pages if any(page.status == repo.DONE for page in pages) else None


def _exportable_conversion(request: Request, conversion_id: int) -> tuple[repo.Conversion, list[repo.Page]]:
    conversion = repo.get_conversion(db_path(request), conversion_id)
    pages = _exportable_pages(request, conversion)
    if pages is None:
        raise HTTPException(409, "This upload has no extracted orders to export yet.")
    return conversion, pages


def _pending_pages_error(pages: list[repo.Page]) -> str | None:
    """Why export must wait, if a page was requeued after the batch finished (e.g. a retry racing the status update)."""
    pending = [page.page_number for page in pages if page.status in (repo.QUEUED, repo.PROCESSING)]
    if not pending:
        return None
    if len(pending) == 1:
        wait_text = "is still being read. Wait for it to finish before exporting, so its order is included."
    else:
        wait_text = "are still being read. Wait for them to finish before exporting, so their orders are included."
    return f"{pages_text(pending)} {wait_text}"


def _choose_template_page(request: Request, conversion: repo.Conversion, error: str | None = None, status_code=200):
    context = {
        "conversion": conversion,
        "templates": repo.list_templates(db_path(request)),
        "selected_template_id": conversion.template_id,
        "error": error,
        "batch_url": batch_url(conversion.id),
    }
    return render(request, "choose_template.html", context, status_code=status_code)


@router.get("/conversions/{conversion_id}/template")
def choose_template(request: Request, conversion_id: int):
    conversion = repo.get_conversion(db_path(request), conversion_id)
    if _exportable_pages(request, conversion) is None:
        return redirect(batch_url(conversion_id))
    return _choose_template_page(request, conversion)


@router.post("/conversions/{conversion_id}/template")
def set_conversion_template(request: Request, conversion_id: int, template_id: str = Form("")):
    conversion, _ = _exportable_conversion(request, conversion_id)
    try:
        template = repo.get_template(db_path(request), int(template_id))
    except (ValueError, repo.NotFoundError):
        return _choose_template_page(request, conversion, "Choose one of the saved templates.", 422)
    repo.set_template(db_path(request), conversion_id, template.id)
    return redirect(f"{batch_url(conversion_id)}/preview")


def _batch_preview(pages: list[repo.Page], template: repo.Template) -> BatchPreview:
    entries = [(page.page_number, page.order, _page_review(page)) for page in pages if page.status == repo.DONE]
    return build_batch_preview(
        entries,
        template.headers,
        template.column_map,
        failed_pages=[page.page_number for page in pages if page.status == repo.FAILED],
        skipped_pages=[page.page_number for page in pages if page.status in SKIPPED_STATUSES],
    )


def _preview_page(
    request: Request,
    conversion: repo.Conversion,
    pages: list[repo.Page],
    export_format: str = "xlsx",
    export_error: str | None = None,
):
    template = repo.get_template(db_path(request), conversion.template_id)
    preview = _batch_preview(pages, template)
    if export_error is None:
        export_error = _pending_pages_error(pages)
    if export_error is None and not template.column_map:
        export_error = f'The template "{template.name}" has no columns mapped yet. Edit its mapping first.'
    context = {
        "conversion": conversion,
        "template": template,
        "preview": preview,
        "unchecked_count": preview.unchecked_count,
        "item_table_warnings": preview.item_table_warnings,
        "orders_needing_attention": preview.orders_needing_attention,
        "failed_pages": preview.failed_pages,
        "skipped_pages": preview.skipped_pages,
        "duplicate_order_refs": preview.duplicate_order_refs,
        "needs_acknowledgement": preview.needs_acknowledgement,
        "export_error": export_error,
        "format": export_format,
        "batch_url": batch_url(conversion.id),
        "template_url": f"{batch_url(conversion.id)}/template",
    }
    return render(request, "preview.html", context, status_code=422 if export_error else 200)


@router.get("/conversions/{conversion_id}/preview")
def preview_conversion(request: Request, conversion_id: int):
    conversion = repo.get_conversion(db_path(request), conversion_id)
    pages = _exportable_pages(request, conversion)
    if pages is None:
        return redirect(batch_url(conversion_id))
    if conversion.template_id is None:
        return redirect(f"{batch_url(conversion_id)}/template")
    return _preview_page(request, conversion, pages)


# def _export_file_stem(conversion: repo.Conversion) -> str:
#     order_ref = conversion.order.order_ref if conversion.order else None
#     return re.sub(r"[^A-Za-z0-9._-]", "_", order_ref) if order_ref else f"conversion-{conversion.id}"


def _safe_file_stem(text: str | None) -> str | None:
    stem = _UNSAFE_FILENAME_CHARS.sub("_", text or "").strip("._")
    # Nothing left but separators (e.g. a fully non-Latin name) is not a usable name.
    return stem if stem.strip("_-") else None


def _export_file_stem(conversion: repo.Conversion, orders: list[Order]) -> str:
    """One order: its order_ref (as in v1). Several: the uploaded file's name. Fallback: conversion-{id}."""
    single_ref = _safe_file_stem(orders[0].order_ref) if len(orders) == 1 else None
    return single_ref or _safe_file_stem(Path(conversion.original_filename).stem) or f"conversion-{conversion.id}"


@router.post("/conversions/{conversion_id}/export")
def export_conversion(
    request: Request,
    conversion_id: int,
    format: str = Form("xlsx"),
    acknowledge_unchecked: str | None = Form(None),
):
    conversion, pages = _exportable_conversion(request, conversion_id)
    if conversion.template_id is None:
        return redirect(f"{batch_url(conversion_id)}/template")
    if format not in EXPORT_MEDIA_TYPES:
        return _preview_page(request, conversion, pages, "xlsx", "Choose Excel (.xlsx) or CSV (.csv).")
    template = repo.get_template(db_path(request), conversion.template_id)
    if not template.column_map:
        return _preview_page(request, conversion, pages, format)
    # Not acknowledgeable: the pending page's order would be silently missing from the file.
    pending_error = _pending_pages_error(pages)
    if pending_error:
        return _preview_page(request, conversion, pages, format, pending_error)
    # v1 single-order gate:
    # review = _review(conversion.order, conversion.confirmed_fields, conversion.profile_name)
    # preview = build_preview(conversion.order, review, template.headers, template.column_map)
    # if preview.needs_acknowledgement and not acknowledge_unchecked:
    #     return _preview_page(request, conversion, format, preview.acknowledgement_reason())
    preview = _batch_preview(pages, template)
    if preview.needs_acknowledgement and not acknowledge_unchecked:
        return _preview_page(request, conversion, pages, format, preview.acknowledgement_reason())

    orders = [page.order for page in pages if page.status == repo.DONE]
    export_dir = repo.conversion_dir(data_dir(request), conversion_id) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_export_file_stem(conversion, orders)}.{format}"
    output_path = export_dir / filename
    if format == "xlsx":
        try:
            write_xlsx(orders, data_dir(request) / template.file_path, output_path, template.column_map)
        except ValueError as exc:
            return _preview_page(request, conversion, pages, format, f"The template could not be filled: {exc}")
    else:
        write_csv(orders, output_path, template.headers, template.column_map)
    return FileResponse(output_path, media_type=EXPORT_MEDIA_TYPES[format], filename=filename)
