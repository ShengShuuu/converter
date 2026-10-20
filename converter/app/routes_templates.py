"""Excel template routes: upload, column-mapping editor, delete. Plain def handlers (SQLite, filesystem, openpyxl)."""

import sqlite3
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from openpyxl import load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from starlette.datastructures import FormData

from converter.app.web import data_dir, db_path, redirect, render, safe_next, submitted_form
from converter.export.xlsx import generate_blank_template, read_template_headers
from converter.mapping import CANONICAL_FIELDS, LITERAL_PREFIX, literal_text, suggest_column_map
from converter.store import repositories as repo

router = APIRouter()

MAX_TEMPLATE_BYTES = 10 * 1024 * 1024
MAX_TEMPLATE_NAME_LENGTH = 120
XLSX_SIGNATURE = b"PK\x03\x04"
MAX_MANUAL_COLUMNS = 200
MAX_HEADER_LENGTH = 255
# Excel's own default name for a new workbook's first sheet.
MANUAL_TEMPLATE_SHEET_NAME = "Sheet1"
# Form-only choice value: the row's typed text is stored as a LITERAL_PREFIX column_map value.
CUSTOM_FIELD_CHOICE = "__custom__"
CUSTOM_FIELD_LABEL = "Custom / fill in manually"


def _clean_cell_text(value: str) -> str:
    # Control characters are invalid in xlsx XML; strip them the same way write_xlsx does.
    return ILLEGAL_CHARACTERS_RE.sub("", value).strip()


def _field_choice_error(header: str, field_choice: str, custom_text: str) -> str | None:
    if field_choice == CUSTOM_FIELD_CHOICE:
        if not custom_text:
            return f'Enter a value for the custom column "{header}", or choose a different option.'
        if len(custom_text) > MAX_HEADER_LENGTH:
            return f"Custom values can be at most {MAX_HEADER_LENGTH} characters."
        return None
    if field_choice and field_choice not in CANONICAL_FIELDS:
        return f"Unknown field for column {header!r}."
    return None


def _column_map_value(field_choice: str, custom_text: str) -> str:
    """The stored column_map value for a validated, non-empty field choice."""
    return LITERAL_PREFIX + custom_text if field_choice == CUSTOM_FIELD_CHOICE else field_choice


def _templates_page(request: Request, next_url: str | None, error: str | None = None, form_name: str = ""):
    context = {"templates": repo.list_templates(db_path(request)), "next": next_url, "error": error, "form_name": form_name}
    return render(request, "templates_list.html", context, status_code=422 if error else 200)


def _with_next(url: str, next_url: str | None) -> str:
    return f"{url}?next={quote(next_url, safe='/')}" if next_url else url


@router.get("/templates")
def templates_page(request: Request, next: str | None = None):
    return _templates_page(request, safe_next(next))


@router.post("/templates")
def upload_template(
    request: Request,
    name: str = Form(""),
    file: UploadFile | None = File(None),
    next: str = Form(""),
):
    next_url = safe_next(next)
    name = name.strip()
    if not name or len(name) > MAX_TEMPLATE_NAME_LENGTH:
        return _templates_page(request, next_url, f"Enter a name of up to {MAX_TEMPLATE_NAME_LENGTH} characters.", name)
    if file is None or Path(file.filename or "").suffix.lower() != ".xlsx":
        return _templates_page(request, next_url, "Upload an Excel .xlsx file.", name)
    content = file.file.read(MAX_TEMPLATE_BYTES + 1)
    if len(content) > MAX_TEMPLATE_BYTES:
        return _templates_page(request, next_url, "The template file is larger than 10 MB.", name)
    if not content.startswith(XLSX_SIGNATURE):
        return _templates_page(request, next_url, "This file is not a valid .xlsx workbook.", name)
    try:
        sheet_name, headers = read_template_headers(load_workbook(BytesIO(content)))
    except ValueError as exc:
        return _templates_page(request, next_url, str(exc), name)
    except Exception:
        # Malformed upload boundary: openpyxl raises many different exception types for corrupt workbooks.
        return _templates_page(request, next_url, "This file could not be read as an Excel workbook.", name)

    try:
        template = repo.create_template(db_path(request), name, sheet_name, headers, suggest_column_map(headers))
    except sqlite3.IntegrityError:
        return _templates_page(request, next_url, f'A template named "{name}" already exists.', name)
    # template_path = data_dir(request) / template.file_path
    # try:
    #     template_path.parent.mkdir(parents=True, exist_ok=True)
    #     template_path.write_bytes(content)
    # except OSError:
    #     repo.delete_template(db_path(request), data_dir(request), template.id)
    #     raise
    _write_template_file(request, template, content)
    return redirect(_with_next(f"/templates/{template.id}", next_url))


def _write_template_file(request: Request, template: repo.Template, content: bytes) -> None:
    template_path = data_dir(request) / template.file_path
    try:
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_bytes(content)
    except OSError:
        # A row without its .xlsx would fail at export time; drop it so the user can simply retry.
        repo.delete_template(db_path(request), data_dir(request), template.id)
        raise


def _manual_builder_page(
    request: Request, next_url: str | None, error: str | None = None, form_name: str = "", rows: list[dict] | None = None
):
    context = {
        "field_options": [{"value": path, "label": label} for path, label in CANONICAL_FIELDS.items()],
        "custom_choice": CUSTOM_FIELD_CHOICE,
        "custom_label": CUSTOM_FIELD_LABEL,
        "rows": rows or [{"header": "", "selected": "", "text": ""}],
        "next": next_url,
        "error": error,
        "form_name": form_name,
    }
    return render(request, "template_manual_builder.html", context, status_code=422 if error else 200)


def _manual_builder_error(manual_columns: list[tuple[str, str, str]]) -> str | None:
    headers = [header for header, _, _ in manual_columns if header]
    if not headers:
        return "Add at least one column header."
    if len(manual_columns) > MAX_MANUAL_COLUMNS:
        return f"A template can have at most {MAX_MANUAL_COLUMNS} columns."
    for header, field_choice, custom_text in manual_columns:
        if not header and field_choice:
            # filled_with = CANONICAL_FIELDS.get(field_path, field_path)
            if field_choice == CUSTOM_FIELD_CHOICE:
                filled_with = custom_text or CUSTOM_FIELD_LABEL
            else:
                filled_with = CANONICAL_FIELDS.get(field_choice, field_choice)
            return f'Enter a column header for the column filled with "{filled_with}".'
        if len(header) > MAX_HEADER_LENGTH:
            return f"Column headers can be at most {MAX_HEADER_LENGTH} characters."
        # if field_path and field_path not in CANONICAL_FIELDS:
        #     return f"Unknown field for column {header!r}."
        choice_error = _field_choice_error(header, field_choice, custom_text)
        if choice_error:
            return choice_error
    seen = set()
    for header in headers:
        if header in seen:
            return f'The column header "{header}" is used more than once.'
        seen.add(header)
    return None


# Declared before /templates/{template_id} so "new-manual" is not parsed as an int id (422).
@router.get("/templates/new-manual")
def manual_builder_page(request: Request, next: str | None = None):
    return _manual_builder_page(request, safe_next(next))


@router.post("/templates/manual")
def create_manual_template(request: Request, form: FormData = Depends(submitted_form)):
    next_url = safe_next(form.get("next"))
    name = str(form.get("name", "")).strip()
    raw_headers = [str(value) for value in form.getlist("header")]
    raw_fields = [str(value) for value in form.getlist("field")]
    # One per row from the page; a client that never offers custom text may omit them all.
    raw_texts = [str(value) for value in form.getlist("field_text")] or [""] * len(raw_headers)
    # manual_columns = [
    #     (ILLEGAL_CHARACTERS_RE.sub("", header).strip(), field_path)
    #     for header, field_path in zip(raw_headers, raw_fields)
    # ]
    # rows = [{"header": header, "selected": field_path} for header, field_path in manual_columns]
    manual_columns = [
        (_clean_cell_text(header), field_choice, _clean_cell_text(custom_text))
        for header, field_choice, custom_text in zip(raw_headers, raw_fields, raw_texts)
    ]
    rows = [
        {"header": header, "selected": field_choice, "text": custom_text}
        for header, field_choice, custom_text in manual_columns
    ]
    if not name or len(name) > MAX_TEMPLATE_NAME_LENGTH:
        return _manual_builder_page(
            request, next_url, f"Enter a name of up to {MAX_TEMPLATE_NAME_LENGTH} characters.", name, rows
        )
    # if len(raw_headers) != len(raw_fields):
    if not len(raw_headers) == len(raw_fields) == len(raw_texts):
        return _manual_builder_page(request, next_url, "Every column needs a header and a field choice.", name, rows)
    error = _manual_builder_error(manual_columns)
    if error:
        return _manual_builder_page(request, next_url, error, name, rows)

    # Blank rows are leftovers from "Add column"; unmapped columns with a header stay in the sheet.
    # kept_columns = [(header, field_path) for header, field_path in manual_columns if header]
    # headers = [header for header, _ in kept_columns]
    # column_map = {header: field_path for header, field_path in kept_columns if field_path}
    kept_columns = [column for column in manual_columns if column[0]]
    headers = [header for header, _, _ in kept_columns]
    column_map = {
        header: _column_map_value(field_choice, custom_text)
        for header, field_choice, custom_text in kept_columns
        if field_choice
    }
    # Build the workbook before the DB write so a failure here never leaves a template row with no file.
    content = generate_blank_template(headers, MANUAL_TEMPLATE_SHEET_NAME)
    try:
        template = repo.create_template(db_path(request), name, MANUAL_TEMPLATE_SHEET_NAME, headers, column_map)
    except sqlite3.IntegrityError:
        return _manual_builder_page(request, next_url, f'A template named "{name}" already exists.', name, rows)
    _write_template_file(request, template, content)
    return redirect(next_url or "/templates")


def _mapping_page(
    request: Request,
    template: repo.Template,
    next_url: str | None,
    error: str | None = None,
    column_map: dict[str, str] | None = None,
):
    """column_map overrides the saved one, so a rejected submit is shown back as the user left it."""
    shown_map = template.column_map if column_map is None else column_map
    suggestion = suggest_column_map(template.headers)
    rows = []
    for index, header in enumerate(template.headers):
        # selected = template.column_map.get(header, "")
        selected = shown_map.get(header, "")
        custom_text = literal_text(selected)
        rows.append(
            {
                "index": index,
                "header": header,
                "selected": CUSTOM_FIELD_CHOICE if custom_text is not None else selected,
                "text": custom_text or "",
                # Tag only when the current choice is still the automatic one.
                "suggested": selected if selected and suggestion.get(header) == selected else None,
            }
        )
    context = {
        "template": template,
        "rows": rows,
        "field_options": [{"value": path, "label": label} for path, label in CANONICAL_FIELDS.items()],
        "custom_choice": CUSTOM_FIELD_CHOICE,
        "custom_label": CUSTOM_FIELD_LABEL,
        "next": next_url,
        "error": error,
    }
    return render(request, "template_mapping.html", context, status_code=422 if error else 200)


@router.get("/templates/{template_id}")
def mapping_page(request: Request, template_id: int, next: str | None = None):
    return _mapping_page(request, repo.get_template(db_path(request), template_id), safe_next(next))


@router.post("/templates/{template_id}/mapping")
def save_mapping(request: Request, template_id: int, form: FormData = Depends(submitted_form)):
    template = repo.get_template(db_path(request), template_id)
    next_url = safe_next(form.get("next"))
    column_map = {}
    error = None
    # for index, header in enumerate(template.headers):
    #     field_path = form.get(f"map_{index}", "")
    #     if field_path and field_path not in CANONICAL_FIELDS:
    #         return _mapping_page(request, template, next_url, f"Unknown field for column {header!r}.")
    #     if field_path:
    #         column_map[header] = field_path
    for index, header in enumerate(template.headers):
        field_choice = str(form.get(f"map_{index}", ""))
        custom_text = _clean_cell_text(str(form.get(f"map_text_{index}", "")))
        error = error or _field_choice_error(header, field_choice, custom_text)
        if field_choice:
            column_map[header] = _column_map_value(field_choice, custom_text)
    if error:
        return _mapping_page(request, template, next_url, error, column_map)
    repo.update_column_map(db_path(request), template_id, column_map)
    return redirect(next_url or "/templates")


@router.delete("/templates/{template_id}")
def delete_template(request: Request, template_id: int):
    repo.delete_template(db_path(request), data_dir(request), template_id)
    # 200 with an empty body, not 204: htmx 2 does not swap a 204, so the list row would stay.
    return Response(status_code=200)
