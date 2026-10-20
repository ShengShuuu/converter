"""SQLite store: schema init, crash recovery, pages, Order JSON round-trip, and the update_order write lock."""

import sqlite3
import threading
from contextlib import closing
from datetime import datetime
from decimal import Decimal

import pytest

from converter.models import BBox, Order, OrderLine
from converter.store import repositories as repo
from converter.store.db import INTERRUPTED_ERROR, PAGE_INTERRUPTED_ERROR, connect, init_db, recover_interrupted


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "converter.db"
    init_db(path)
    return path


def make_order() -> Order:
    return Order(
        order_ref="S08824",
        order_date=datetime(2026, 4, 22, 10, 41, 30),
        customer="ACME TRADING CORP.",
        untaxed_total=Decimal("7464.29"),
        total=Decimal("8360.00"),
        source_pages=[1],
        flags=["low_confidence:customer"],
        lines=[
            OrderLine(
                product="PX-S100 / 100W PX-S SOLAR FLOOD LIGHT",
                quantity=Decimal("2.00"),
                unit_price=Decimal("2880.00"),
                subtotal=Decimal("5760.00"),
                taxes="Output Tax (12%)",
                confidence={"product": 0.91},
                bbox=BBox(page=1, left=10, top=20, width=300, height=40),
            ),
            OrderLine(product="Blank amounts stay blank"),
        ],
    )


# def extracted_conversion(db_path, order: Order | None = None) -> int:
#     conversion = repo.create_conversion(db_path, "scan.pdf", ".pdf")
#     repo.save_extraction(db_path, conversion.id, order or make_order(), "odoo_sale_order_v1")
#     return conversion.id
def extracted_conversion(db_path, order: Order | None = None, pages: int = 1) -> int:
    """A conversion whose page 1 holds an extracted order (other pages stay queued)."""
    conversion = repo.create_conversion(db_path, "scan.pdf", ".pdf")
    repo.create_pages(db_path, conversion.id, pages)
    repo.save_extraction(db_path, conversion.id, 1, order or make_order(), "odoo_sale_order_v1")
    return conversion.id


def page_one(db_path, conversion_id: int) -> repo.Page:
    return repo.get_page(db_path, conversion_id, 1)


def test_order_round_trips_decimal_and_datetime_exactly(db_path):
    original = make_order()
    stored = page_one(db_path, extracted_conversion(db_path, original))
    assert stored.order == original
    assert stored.extracted_order == original
    assert isinstance(stored.order.total, Decimal) and str(stored.order.total) == "8360.00"
    assert isinstance(stored.order.lines[0].quantity, Decimal) and str(stored.order.lines[0].quantity) == "2.00"
    assert stored.order.order_date == datetime(2026, 4, 22, 10, 41, 30)
    assert stored.order.lines[1].subtotal is None and stored.order.lines[1].taxes is None


def test_init_db_is_idempotent(db_path):
    conversion = repo.create_conversion(db_path, "scan.pdf", ".pdf")
    init_db(db_path)
    assert repo.get_conversion(db_path, conversion.id).original_filename == "scan.pdf"
    with closing(connect(db_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_source_path_is_relative_to_the_data_dir(db_path):
    conversion = repo.create_conversion(db_path, "photo.JPG", ".jpg")
    assert conversion.source_path == f"conversions/{conversion.id}/source.jpg"
    assert conversion.status == repo.QUEUED


def test_recover_interrupted_fails_only_unfinished_jobs(db_path):
    ids = {status: repo.create_conversion(db_path, f"{status}.pdf", ".pdf").id for status in
           ("queued", "processing", "done", "failed")}
    for status, conversion_id in ids.items():
        repo.set_status(db_path, conversion_id, status, error="earlier" if status == "failed" else None)

    assert recover_interrupted(db_path) == 2

    for status in ("queued", "processing"):
        recovered = repo.get_conversion(db_path, ids[status])
        assert (recovered.status, recovered.error) == ("failed", INTERRUPTED_ERROR)
    assert repo.get_conversion(db_path, ids["done"]).status == "done"
    assert repo.get_conversion(db_path, ids["failed"]).error == "earlier"


def test_recover_interrupted_fails_only_unfinished_pages(db_path):
    conversion_id = repo.create_conversion(db_path, "batch.pdf", ".pdf").id
    statuses = ["queued", "processing", "done", "not_sales_order", "skipped", "failed"]
    repo.create_pages(db_path, conversion_id, len(statuses))
    repo.set_status(db_path, conversion_id, "processing")
    for number, status in enumerate(statuses, start=1):
        if status == "done":
            repo.save_extraction(db_path, conversion_id, number, make_order(), "odoo_sale_order_v1")
        else:
            repo.set_page_status(db_path, conversion_id, number, status, error="earlier" if status == "failed" else None)

    recover_interrupted(db_path)

    pages = {page.page_number: page for page in repo.list_pages(db_path, conversion_id)}
    for number in (1, 2):
        assert (pages[number].status, pages[number].error) == ("failed", PAGE_INTERRUPTED_ERROR)
    assert [pages[number].status for number in (3, 4, 5, 6)] == ["done", "not_sales_order", "skipped", "failed"]
    assert pages[3].order == make_order() and pages[6].error == "earlier"
    assert repo.get_conversion(db_path, conversion_id).status == "failed"


def test_save_extraction_resets_confirmations(db_path):
    conversion_id = extracted_conversion(db_path)
    repo.update_order(db_path, conversion_id, 1, lambda order, confirmed: confirmed.add("total"))
    repo.save_extraction(db_path, conversion_id, 1, make_order(), "odoo_sale_order_v1")
    assert page_one(db_path, conversion_id).confirmed_fields == frozenset()


def test_update_order_keeps_extracted_copy_untouched(db_path):
    conversion_id = extracted_conversion(db_path)

    def edit(order, confirmed):
        order.total = Decimal("1.00")
        confirmed.add("total")

    order, confirmed = repo.update_order(db_path, conversion_id, 1, edit)
    stored = page_one(db_path, conversion_id)
    assert order.total == stored.order.total == Decimal("1.00")
    assert confirmed == stored.confirmed_fields == {"total"}
    assert stored.extracted_order.total == Decimal("8360.00")


def test_update_order_rolls_back_when_mutate_raises(db_path):
    conversion_id = extracted_conversion(db_path)

    def failing_edit(order, confirmed):
        order.total = Decimal("1.00")
        confirmed.add("total")
        raise RuntimeError("abort")

    with pytest.raises(RuntimeError):
        repo.update_order(db_path, conversion_id, 1, failing_edit)
    stored = page_one(db_path, conversion_id)
    assert stored.order.total == Decimal("8360.00") and stored.confirmed_fields == frozenset()


def test_update_order_rejects_values_of_the_wrong_type(db_path):
    conversion_id = extracted_conversion(db_path)
    with pytest.raises(ValueError):
        repo.update_order(db_path, conversion_id, 1, lambda order, confirmed: setattr(order, "total", "not money"))


def test_concurrent_update_order_calls_keep_both_edits(db_path):
    """The BEGIN IMMEDIATE lock serializes read-modify-write, so a second autosave sees the first one's edit."""
    conversion_id = extracted_conversion(db_path)
    first_holds_lock = threading.Event()
    total_seen_by_second: list[Decimal] = []
    errors: list[BaseException] = []

    def edit_total(order, confirmed):
        first_holds_lock.set()
        threading.Event().wait(0.3)
        order.total = Decimal("100.00")
        confirmed.add("total")

    def edit_customer(order, confirmed):
        total_seen_by_second.append(order.total)
        order.customer = "EDITED CUSTOMER"
        confirmed.add("customer")

    def run(mutate):
        try:
            repo.update_order(db_path, conversion_id, 1, mutate)
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=run, args=(edit_total,))
    first.start()
    assert first_holds_lock.wait(5)
    second = threading.Thread(target=run, args=(edit_customer,))
    second.start()
    first.join(10)
    second.join(10)

    assert errors == []
    stored = page_one(db_path, conversion_id)
    assert stored.order.total == Decimal("100.00")
    assert stored.order.customer == "EDITED CUSTOMER"
    assert stored.confirmed_fields == {"total", "customer"}
    assert total_seen_by_second == [Decimal("100.00")]


def test_unlocked_read_modify_write_loses_an_edit(db_path):
    """Control for the test above: without the lock the same interleaving drops the second edit."""
    conversion_id = extracted_conversion(db_path)
    stale = page_one(db_path, conversion_id).order

    fresh = page_one(db_path, conversion_id).order
    fresh.customer = "EDITED CUSTOMER"
    with closing(connect(db_path)) as connection:
        connection.execute("UPDATE pages SET order_json = ? WHERE conversion_id = ?", (fresh.model_dump_json(), conversion_id))
    stale.total = Decimal("100.00")
    with closing(connect(db_path)) as connection:
        connection.execute("UPDATE pages SET order_json = ? WHERE conversion_id = ?", (stale.model_dump_json(), conversion_id))

    assert page_one(db_path, conversion_id).order.customer == "ACME TRADING CORP."


def test_delete_conversion_removes_row_and_directory(db_path, tmp_path):
    conversion_id = extracted_conversion(db_path)
    directory = repo.conversion_dir(tmp_path, conversion_id)
    directory.mkdir(parents=True)
    (directory / "display.png").write_bytes(b"x")

    repo.delete_conversion(db_path, tmp_path, conversion_id)

    assert not directory.exists()
    with pytest.raises(repo.NotFoundError):
        repo.get_conversion(db_path, conversion_id)


def test_templates_crud_and_conversion_link_cleared_on_delete(db_path, tmp_path):
    template = repo.create_template(db_path, "Odoo export", "Converted", ["Customer"], {"Customer": "order.customer"})
    assert template.file_path == f"templates/{template.id}.xlsx"
    (tmp_path / "templates").mkdir()
    (tmp_path / template.file_path).write_bytes(b"PK")
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_template(db_path, "Odoo export", "Sheet1", [], {})

    repo.update_column_map(db_path, template.id, {})
    assert repo.get_template(db_path, template.id).column_map == {}
    assert [t.name for t in repo.list_templates(db_path)] == ["Odoo export"]

    conversion_id = extracted_conversion(db_path)
    repo.set_template(db_path, conversion_id, template.id)
    repo.delete_template(db_path, tmp_path, template.id)

    assert not (tmp_path / template.file_path).exists()
    assert repo.get_conversion(db_path, conversion_id).template_id is None
    with pytest.raises(repo.NotFoundError):
        repo.get_template(db_path, template.id)


def test_deleted_ids_are_never_reused(db_path, tmp_path):
    first = extracted_conversion(db_path)
    deleted = extracted_conversion(db_path)
    repo.delete_conversion(db_path, tmp_path, deleted)
    assert extracted_conversion(db_path) > deleted > first

    template = repo.create_template(db_path, "Old", "Sheet1", ["Customer"], {})
    repo.delete_template(db_path, tmp_path, template.id)
    assert repo.create_template(db_path, "New", "Sheet1", ["Customer"], {}).id > template.id


# --- pages ---


def test_create_pages_sets_page_count_and_queues_every_page(db_path):
    conversion_id = repo.create_conversion(db_path, "batch.pdf", ".pdf").id
    assert repo.get_conversion(db_path, conversion_id).page_count is None

    repo.create_pages(db_path, conversion_id, 3)

    assert repo.get_conversion(db_path, conversion_id).page_count == 3
    pages = repo.list_pages(db_path, conversion_id)
    assert [(page.page_number, page.status, page.order) for page in pages] == [(n, "queued", None) for n in (1, 2, 3)]
    assert all(page.confirmed_fields == frozenset() for page in pages)


def test_create_pages_for_a_missing_conversion_inserts_nothing(db_path):
    with pytest.raises(repo.NotFoundError):
        repo.create_pages(db_path, 999, 2)
    with closing(connect(db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 0


def test_claim_next_page_takes_lowest_queued_page_until_none_left(db_path):
    conversion_id = repo.create_conversion(db_path, "batch.pdf", ".pdf").id
    other_id = repo.create_conversion(db_path, "other.pdf", ".pdf").id
    repo.create_pages(db_path, conversion_id, 3)
    repo.create_pages(db_path, other_id, 1)
    repo.set_page_status(db_path, conversion_id, 1, "done")

    assert repo.claim_next_page(db_path, conversion_id) == 2
    assert repo.get_page(db_path, conversion_id, 2).status == "processing"
    assert repo.claim_next_page(db_path, conversion_id) == 3
    assert repo.claim_next_page(db_path, conversion_id) is None
    assert repo.claim_next_page(db_path, 999) is None
    assert repo.get_page(db_path, other_id, 1).status == "queued"


def test_page_number_must_be_unique_per_conversion(db_path):
    conversion_id = repo.create_conversion(db_path, "batch.pdf", ".pdf").id
    repo.create_pages(db_path, conversion_id, 1)
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_pages(db_path, conversion_id, 1)


def test_deleting_a_conversion_cascades_to_its_pages(db_path, tmp_path):
    conversion_id = extracted_conversion(db_path, pages=3)
    kept_id = extracted_conversion(db_path, pages=2)

    repo.delete_conversion(db_path, tmp_path, conversion_id)

    with closing(connect(db_path)) as connection:
        remaining = connection.execute("SELECT conversion_id, COUNT(*) FROM pages GROUP BY conversion_id").fetchall()
    assert [tuple(row) for row in remaining] == [(kept_id, 2)]
    with pytest.raises(repo.NotFoundError):
        repo.get_page(db_path, conversion_id, 1)


def test_update_order_on_one_page_leaves_other_pages_untouched(db_path):
    conversion_id = extracted_conversion(db_path, pages=3)
    repo.save_extraction(db_path, conversion_id, 3, make_order(), "odoo_sale_order_v1")

    def edit(order, confirmed):
        order.total = Decimal("1.00")
        order.lines.pop()
        confirmed.add("total")

    repo.update_order(db_path, conversion_id, 3, edit)

    first, third = page_one(db_path, conversion_id), repo.get_page(db_path, conversion_id, 3)
    assert first.order == make_order() and first.confirmed_fields == frozenset()
    assert third.order.total == Decimal("1.00") and len(third.order.lines) == 1
    assert third.confirmed_fields == {"total"}
    assert third.extracted_order == make_order()


def test_update_order_on_a_page_without_an_order_is_not_found(db_path):
    conversion_id = extracted_conversion(db_path, pages=2)
    with pytest.raises(repo.NotFoundError):
        repo.update_order(db_path, conversion_id, 2, lambda order, confirmed: None)
    with pytest.raises(repo.NotFoundError):
        repo.update_order(db_path, conversion_id, 7, lambda order, confirmed: None)


def test_page_writes_to_a_missing_page_raise_not_found(db_path):
    conversion_id = extracted_conversion(db_path)
    with pytest.raises(repo.NotFoundError):
        repo.set_page_status(db_path, conversion_id, 2, "failed")
    with pytest.raises(repo.NotFoundError):
        repo.save_page_info(db_path, conversion_id, 2, 0.5, 10, 20, 0)
    with pytest.raises(repo.NotFoundError):
        repo.save_extraction(db_path, 999, 1, make_order(), "odoo_sale_order_v1")


def test_save_page_info_is_per_page(db_path):
    conversion_id = extracted_conversion(db_path, pages=2)
    repo.save_page_info(db_path, conversion_id, 2, 0.25, 30, 40, 90)
    second = repo.get_page(db_path, conversion_id, 2)
    assert (second.classifier_score, second.page_width, second.page_height, second.rotated_by) == (0.25, 30, 40, 90)
    assert page_one(db_path, conversion_id).classifier_score is None


def test_requeue_failed_pages_all_or_one(db_path):
    conversion_id = extracted_conversion(db_path, pages=4)
    for number in (2, 3, 4):
        repo.set_page_status(db_path, conversion_id, number, "failed", error="boom")

    assert repo.requeue_failed_pages(db_path, conversion_id, 3) == 1
    assert [p.status for p in repo.list_pages(db_path, conversion_id)] == ["done", "failed", "queued", "failed"]
    assert repo.get_page(db_path, conversion_id, 3).error is None
    assert repo.requeue_failed_pages(db_path, conversion_id, 1) == 0

    assert repo.requeue_failed_pages(db_path, conversion_id) == 2
    assert [p.status for p in repo.list_pages(db_path, conversion_id)] == ["done", "queued", "queued", "queued"]
    assert page_one(db_path, conversion_id).order == make_order()


def test_mark_page_skipped_only_from_finished_states(db_path):
    conversion_id = extracted_conversion(db_path, pages=4)
    repo.set_page_status(db_path, conversion_id, 2, "failed", error="boom")
    repo.set_page_status(db_path, conversion_id, 3, "processing")

    assert repo.mark_page_skipped(db_path, conversion_id, 1) is True
    assert repo.mark_page_skipped(db_path, conversion_id, 2) is True
    assert repo.mark_page_skipped(db_path, conversion_id, 3) is False
    assert repo.mark_page_skipped(db_path, conversion_id, 4) is False
    assert repo.mark_page_skipped(db_path, conversion_id, 9) is False

    pages = repo.list_pages(db_path, conversion_id)
    assert [p.status for p in pages] == ["skipped", "skipped", "processing", "queued"]
    assert pages[1].error is None


def test_page_summary_counts_every_status(db_path):
    conversion_id = extracted_conversion(db_path, pages=5)
    repo.set_page_status(db_path, conversion_id, 2, "processing")
    repo.set_page_status(db_path, conversion_id, 3, "not_sales_order")

    summary = repo.page_summary(db_path, conversion_id)

    assert summary.counts == {"queued": 2, "processing": 1, "done": 1, "not_sales_order": 1, "failed": 0, "skipped": 0}
    assert (summary.total, summary.pending, summary.finished) == (5, 3, 2)
    empty = repo.page_summary(db_path, 999)
    assert (empty.total, empty.pending, empty.finished) == (0, 0, 0)


def test_page_dir_is_under_the_conversion_dir(tmp_path):
    assert repo.page_dir(tmp_path, 7, 3) == tmp_path / "conversions" / "7" / "pages" / "3"


def test_stale_v1_database_fails_loudly(tmp_path):
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE conversions(id INTEGER PRIMARY KEY, order_json TEXT); PRAGMA user_version = 1;"
        )

    with pytest.raises(RuntimeError, match=r"schema v1 database found at .*old\.db; delete it and restart"):
        init_db(path)


def test_page_status_check_constraint_rejects_unknown_status(db_path):
    conversion_id = extracted_conversion(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        repo.set_page_status(db_path, conversion_id, 1, "exported")
