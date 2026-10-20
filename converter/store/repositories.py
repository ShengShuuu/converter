"""Conversion, page and template repositories: plain functions, one short connection per call."""

import json
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from converter.models import Order
from converter.store.db import TIMESTAMP_FORMAT, connect, now_timestamp

# ConversionStatus = Literal["queued", "processing", "done", "not_sales_order", "failed"]
ConversionStatus = Literal["queued", "processing", "done", "failed"]
PageStatus = Literal["queued", "processing", "done", "not_sales_order", "failed", "skipped"]
QUEUED: ConversionStatus = "queued"
PROCESSING: ConversionStatus = "processing"
# Conversion level: every page has been attempted. Page level: the page's order was extracted.
DONE: ConversionStatus = "done"
FAILED: ConversionStatus = "failed"
NOT_SALES_ORDER: PageStatus = "not_sales_order"
SKIPPED: PageStatus = "skipped"
PAGE_STATUSES: tuple[PageStatus, ...] = ("queued", "processing", "done", "not_sales_order", "failed", "skipped")
# Skip covers a page the classifier accepted by mistake, or a failed page not worth retrying.
SKIPPABLE_STATUSES: tuple[PageStatus, ...] = ("done", "failed")


class NotFoundError(LookupError):
    """The requested conversion/page/template row does not exist."""


# v1 single-page shape; the per-page fields moved to Page:
# @dataclass(frozen=True)
# class Conversion:
#     id: int
#     original_filename: str
#     source_path: str
#     status: ConversionStatus
#     error: str | None
#     profile_name: str | None
#     classifier_score: float | None
#     rotated_by: int | None
#     page_width: int | None
#     page_height: int | None
#     extracted_order: Order | None
#     order: Order | None
#     confirmed_fields: frozenset[str]
#     template_id: int | None
#     created_at: datetime
#     updated_at: datetime


@dataclass(frozen=True)
class Conversion:
    id: int
    original_filename: str
    source_path: str
    status: ConversionStatus
    error: str | None
    page_count: int | None
    template_id: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Page:
    id: int
    conversion_id: int
    page_number: int
    status: PageStatus
    error: str | None
    profile_name: str | None
    classifier_score: float | None
    rotated_by: int | None
    page_width: int | None
    page_height: int | None
    extracted_order: Order | None
    order: Order | None
    confirmed_fields: frozenset[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PageSummary:
    """Page counts by status, derived from the rows on every read (never stored, so it cannot drift)."""

    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def pending(self) -> int:
        return self.counts["queued"] + self.counts["processing"]

    @property
    def finished(self) -> int:
        return self.total - self.pending


@dataclass(frozen=True)
class Template:
    id: int
    name: str
    file_path: str
    sheet_name: str
    headers: list[str]
    column_map: dict[str, str]
    created_at: datetime

    @property
    def header_count(self) -> int:
        return len(self.headers)


def conversion_dir(data_dir: Path, conversion_id: int) -> Path:
    return data_dir / "conversions" / str(conversion_id)


def page_dir(data_dir: Path, conversion_id: int, page_number: int) -> Path:
    return conversion_dir(data_dir, conversion_id) / "pages" / str(page_number)


def _parse_timestamp(text: str) -> datetime:
    return datetime.strptime(text, TIMESTAMP_FORMAT)


def _order_or_none(order_json: str | None) -> Order | None:
    return Order.model_validate_json(order_json) if order_json is not None else None


def _conversion_from_row(row: sqlite3.Row) -> Conversion:
    return Conversion(
        id=row["id"],
        original_filename=row["original_filename"],
        source_path=row["source_path"],
        status=row["status"],
        error=row["error"],
        page_count=row["page_count"],
        template_id=row["template_id"],
        created_at=_parse_timestamp(row["created_at"]),
        updated_at=_parse_timestamp(row["updated_at"]),
    )


def _page_from_row(row: sqlite3.Row) -> Page:
    return Page(
        id=row["id"],
        conversion_id=row["conversion_id"],
        page_number=row["page_number"],
        status=row["status"],
        error=row["error"],
        profile_name=row["profile_name"],
        classifier_score=row["classifier_score"],
        rotated_by=row["rotated_by"],
        page_width=row["page_width"],
        page_height=row["page_height"],
        extracted_order=_order_or_none(row["extracted_order_json"]),
        order=_order_or_none(row["order_json"]),
        confirmed_fields=frozenset(json.loads(row["confirmed_fields_json"])),
        created_at=_parse_timestamp(row["created_at"]),
        updated_at=_parse_timestamp(row["updated_at"]),
    )


def _template_from_row(row: sqlite3.Row) -> Template:
    return Template(
        id=row["id"],
        name=row["name"],
        file_path=row["file_path"],
        sheet_name=row["sheet_name"],
        headers=json.loads(row["headers_json"]),
        column_map=json.loads(row["column_map_json"]),
        created_at=_parse_timestamp(row["created_at"]),
    )


def _execute_one(db_path: Path, sql: str, params: tuple) -> None:
    with closing(connect(db_path)) as connection:
        if connection.execute(sql, params).rowcount == 0:
            raise NotFoundError(f"no row with id {params[-1]!r}")


def _update_page(db_path: Path, conversion_id: int, page_number: int, assignments: str, params: tuple) -> None:
    """UPDATE one page's columns (plus updated_at); raises NotFoundError if the page (or its conversion) is gone."""
    with closing(connect(db_path)) as connection:
        cursor = connection.execute(
            f"UPDATE pages SET {assignments}, updated_at = ? WHERE conversion_id = ? AND page_number = ?",
            (*params, now_timestamp(), conversion_id, page_number),
        )
        if cursor.rowcount == 0:
            raise NotFoundError(f"conversion {conversion_id} has no page {page_number}")


# --- conversions ---


def create_conversion(db_path: Path, original_filename: str, source_suffix: str) -> Conversion:
    """Insert a queued conversion; its source file lives at data_dir / returned.source_path."""
    timestamp = now_timestamp()
    with closing(connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                "INSERT INTO conversions(original_filename, source_path, status, created_at, updated_at)"
                " VALUES (?, '', ?, ?, ?)",
                (original_filename, QUEUED, timestamp, timestamp),
            )
            conversion_id = cursor.lastrowid
            connection.execute(
                "UPDATE conversions SET source_path = ? WHERE id = ?",
                (f"conversions/{conversion_id}/source{source_suffix}", conversion_id),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return get_conversion(db_path, conversion_id)


def get_conversion(db_path: Path, conversion_id: int) -> Conversion:
    with closing(connect(db_path)) as connection:
        row = connection.execute("SELECT * FROM conversions WHERE id = ?", (conversion_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"conversion {conversion_id} not found")
    return _conversion_from_row(row)


def list_conversions(db_path: Path, limit: int = 20) -> list[Conversion]:
    with closing(connect(db_path)) as connection:
        rows = connection.execute("SELECT * FROM conversions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_conversion_from_row(row) for row in rows]


def set_status(db_path: Path, conversion_id: int, status: ConversionStatus, error: str | None = None) -> None:
    _execute_one(
        db_path,
        "UPDATE conversions SET status = ?, error = ?, updated_at = ? WHERE id = ?",
        (status, error, now_timestamp(), conversion_id),
    )


# v1 save_page_info / save_extraction / update_order were keyed by conversion_id alone; now page-scoped below.


def set_template(db_path: Path, conversion_id: int, template_id: int | None) -> None:
    _execute_one(
        db_path,
        "UPDATE conversions SET template_id = ?, updated_at = ? WHERE id = ?",
        (template_id, now_timestamp(), conversion_id),
    )


def delete_conversion(db_path: Path, data_dir: Path, conversion_id: int) -> None:
    """Deletes the conversion's pages too (ON DELETE CASCADE) and its whole directory."""
    _execute_one(db_path, "DELETE FROM conversions WHERE id = ?", (conversion_id,))
    shutil.rmtree(conversion_dir(data_dir, conversion_id), ignore_errors=True)


# --- pages ---


def create_pages(db_path: Path, conversion_id: int, count: int) -> None:
    """Record the page count and insert one queued row per page, in one transaction."""
    timestamp = now_timestamp()
    with closing(connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                "UPDATE conversions SET page_count = ?, updated_at = ? WHERE id = ?", (count, timestamp, conversion_id)
            )
            if cursor.rowcount == 0:
                raise NotFoundError(f"conversion {conversion_id} not found")
            connection.executemany(
                "INSERT INTO pages(conversion_id, page_number, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                [(conversion_id, number, QUEUED, timestamp, timestamp) for number in range(1, count + 1)],
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise


def claim_next_page(db_path: Path, conversion_id: int) -> int | None:
    """Move the lowest-numbered queued page to processing and return its number; None when none are queued."""
    with closing(connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT page_number FROM pages WHERE conversion_id = ? AND status = ? ORDER BY page_number LIMIT 1",
                (conversion_id, QUEUED),
            ).fetchone()
            if row is not None:
                connection.execute(
                    "UPDATE pages SET status = ?, error = NULL, updated_at = ?"
                    " WHERE conversion_id = ? AND page_number = ?",
                    (PROCESSING, now_timestamp(), conversion_id, row["page_number"]),
                )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return row["page_number"] if row is not None else None


def get_page(db_path: Path, conversion_id: int, page_number: int) -> Page:
    with closing(connect(db_path)) as connection:
        row = connection.execute(
            "SELECT * FROM pages WHERE conversion_id = ? AND page_number = ?", (conversion_id, page_number)
        ).fetchone()
    if row is None:
        raise NotFoundError(f"conversion {conversion_id} has no page {page_number}")
    return _page_from_row(row)


def list_pages(db_path: Path, conversion_id: int) -> list[Page]:
    with closing(connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT * FROM pages WHERE conversion_id = ? ORDER BY page_number", (conversion_id,)
        ).fetchall()
    return [_page_from_row(row) for row in rows]


def page_summary(db_path: Path, conversion_id: int) -> PageSummary:
    with closing(connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT status, COUNT(*) AS status_count FROM pages WHERE conversion_id = ? GROUP BY status",
            (conversion_id,),
        ).fetchall()
    counts = dict.fromkeys(PAGE_STATUSES, 0)
    counts.update({row["status"]: row["status_count"] for row in rows})
    return PageSummary(counts)


def set_page_status(
    db_path: Path, conversion_id: int, page_number: int, status: PageStatus, error: str | None = None
) -> None:
    _update_page(db_path, conversion_id, page_number, "status = ?, error = ?", (status, error))


def save_page_info(
    db_path: Path,
    conversion_id: int,
    page_number: int,
    classifier_score: float,
    page_width: int,
    page_height: int,
    rotated_by: int,
) -> None:
    _update_page(
        db_path,
        conversion_id,
        page_number,
        "classifier_score = ?, page_width = ?, page_height = ?, rotated_by = ?",
        (classifier_score, page_width, page_height, rotated_by),
    )


def save_extraction(db_path: Path, conversion_id: int, page_number: int, order: Order, profile_name: str) -> None:
    """Store a fresh extraction as both the immutable OCR result and the editable copy; resets confirmations."""
    order_json = order.model_dump_json()
    _update_page(
        db_path,
        conversion_id,
        page_number,
        "status = ?, error = NULL, profile_name = ?, extracted_order_json = ?, order_json = ?,"
        " confirmed_fields_json = '[]'",
        (DONE, profile_name, order_json, order_json),
    )


def update_order(
    db_path: Path, conversion_id: int, page_number: int, mutate: Callable[[Order, set[str]], None]
) -> tuple[Order, frozenset[str]]:
    """Read-modify-write of the editable order under BEGIN IMMEDIATE, so concurrent autosaves cannot lose edits."""
    with closing(connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT order_json, confirmed_fields_json FROM pages WHERE conversion_id = ? AND page_number = ?",
                (conversion_id, page_number),
            ).fetchone()
            if row is None or row["order_json"] is None:
                raise NotFoundError(f"conversion {conversion_id} page {page_number} has no extracted order")
            order = Order.model_validate_json(row["order_json"])
            confirmed_fields = set(json.loads(row["confirmed_fields_json"]))
            mutate(order, confirmed_fields)
            # Re-validate: mutate assigns attributes directly, which Pydantic does not check.
            order = Order.model_validate(order.model_dump(warnings=False))
            connection.execute(
                "UPDATE pages SET order_json = ?, confirmed_fields_json = ?, updated_at = ?"
                " WHERE conversion_id = ? AND page_number = ?",
                (order.model_dump_json(), json.dumps(sorted(confirmed_fields)), now_timestamp(),
                 conversion_id, page_number),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return order, frozenset(confirmed_fields)


def requeue_failed_pages(db_path: Path, conversion_id: int, page_number: int | None = None) -> int:
    """Put failed pages (all of them, or only page_number) back in the queue; returns how many were requeued."""
    sql = "UPDATE pages SET status = ?, error = NULL, updated_at = ? WHERE conversion_id = ? AND status = ?"
    params: tuple = (QUEUED, now_timestamp(), conversion_id, FAILED)
    if page_number is not None:
        sql += " AND page_number = ?"
        params += (page_number,)
    with closing(connect(db_path)) as connection:
        return connection.execute(sql, params).rowcount


def mark_page_skipped(db_path: Path, conversion_id: int, page_number: int) -> bool:
    """Skip a page in one of SKIPPABLE_STATUSES; False (nothing changed) for any other state, e.g. still being read."""
    placeholders = ", ".join("?" * len(SKIPPABLE_STATUSES))
    with closing(connect(db_path)) as connection:
        cursor = connection.execute(
            "UPDATE pages SET status = ?, error = NULL, updated_at = ?"
            f" WHERE conversion_id = ? AND page_number = ? AND status IN ({placeholders})",
            (SKIPPED, now_timestamp(), conversion_id, page_number, *SKIPPABLE_STATUSES),
        )
        return cursor.rowcount == 1


def restore_skipped_page(db_path: Path, conversion_id: int, page_number: int) -> bool:
    """Undo a skip of a page that still has its order: back to done with edits and confirmations untouched.

    False (nothing changed) if the page is no longer skipped or never had an order to restore.
    """
    with closing(connect(db_path)) as connection:
        cursor = connection.execute(
            "UPDATE pages SET status = ?, error = NULL, updated_at = ?"
            " WHERE conversion_id = ? AND page_number = ? AND status = ? AND order_json IS NOT NULL",
            (DONE, now_timestamp(), conversion_id, page_number, SKIPPED),
        )
        return cursor.rowcount == 1


# --- templates ---


def create_template(
    db_path: Path, name: str, sheet_name: str, headers: list[str], column_map: dict[str, str]
) -> Template:
    """Insert a template row; its .xlsx lives at data_dir / returned.file_path. Duplicate names raise IntegrityError."""
    with closing(connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                "INSERT INTO templates(name, file_path, sheet_name, headers_json, column_map_json, created_at)"
                " VALUES (?, '', ?, ?, ?, ?)",
                (name, sheet_name, json.dumps(headers), json.dumps(column_map), now_timestamp()),
            )
            template_id = cursor.lastrowid
            connection.execute(
                "UPDATE templates SET file_path = ? WHERE id = ?", (f"templates/{template_id}.xlsx", template_id)
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return get_template(db_path, template_id)


def list_templates(db_path: Path) -> list[Template]:
    with closing(connect(db_path)) as connection:
        rows = connection.execute("SELECT * FROM templates ORDER BY name COLLATE NOCASE").fetchall()
    return [_template_from_row(row) for row in rows]


def get_template(db_path: Path, template_id: int) -> Template:
    with closing(connect(db_path)) as connection:
        row = connection.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"template {template_id} not found")
    return _template_from_row(row)


def update_column_map(db_path: Path, template_id: int, column_map: dict[str, str]) -> None:
    _execute_one(
        db_path, "UPDATE templates SET column_map_json = ? WHERE id = ?", (json.dumps(column_map), template_id)
    )


def delete_template(db_path: Path, data_dir: Path, template_id: int) -> None:
    template = get_template(db_path, template_id)
    _execute_one(db_path, "DELETE FROM templates WHERE id = ?", (template_id,))
    (data_dir / template.file_path).unlink(missing_ok=True)
