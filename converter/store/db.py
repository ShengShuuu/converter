"""SQLite connection setup (WAL, busy timeout, foreign keys), schema creation, and crash recovery."""

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
# SCHEMA_VERSION = 1
SCHEMA_VERSION = 2
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"
INTERRUPTED_ERROR = "Processing was interrupted because the app restarted. Click Retry to process this file again."
PAGE_INTERRUPTED_ERROR = "Reading this page was interrupted because the app restarted. Click Retry to read it again."


def now_timestamp() -> str:
    return datetime.now().strftime(TIMESTAMP_FORMAT)


def connect(db_path: Path) -> sqlite3.Connection:
    """A new connection per operation; isolation_level=None so transactions are explicit (BEGIN IMMEDIATE)."""
    connection = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


# def init_db(db_path: Path) -> None:
#     db_path.parent.mkdir(parents=True, exist_ok=True)
#     with closing(connect(db_path)) as connection:
#         if connection.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
#             return
#         connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        if version > 0:
            # Fail loudly here instead of with "no such column" on the first request.
            raise RuntimeError(
                f"schema v{version} database found at {db_path}; delete it and restart "
                f"(this app expects schema v{SCHEMA_VERSION}; nothing is deployed yet, so there is no migration)"
            )
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


# def recover_interrupted(db_path: Path) -> int:
#     """Fail jobs left queued/processing by a restart; in-process executor state does not survive one."""
#     with closing(connect(db_path)) as connection:
#         cursor = connection.execute(
#             "UPDATE conversions SET status = 'failed', error = ?, updated_at = ?"
#             " WHERE status IN ('queued', 'processing')",
#             (INTERRUPTED_ERROR, now_timestamp()),
#         )
#         return cursor.rowcount


def recover_interrupted(db_path: Path) -> int:
    """Fail conversions and pages left queued/processing by a restart; returns the number of conversions failed.

    In-process executor state does not survive a restart. Finished pages (done, not_sales_order, skipped, failed)
    are left alone, so a retry only re-reads the interrupted ones.
    """
    timestamp = now_timestamp()
    with closing(connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                "UPDATE conversions SET status = 'failed', error = ?, updated_at = ?"
                " WHERE status IN ('queued', 'processing')",
                (INTERRUPTED_ERROR, timestamp),
            )
            connection.execute(
                "UPDATE pages SET status = 'failed', error = ?, updated_at = ? WHERE status IN ('queued', 'processing')",
                (PAGE_INTERRUPTED_ERROR, timestamp),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return cursor.rowcount
