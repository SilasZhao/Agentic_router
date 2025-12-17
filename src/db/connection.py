from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator


def _dict_row_factory(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    """
    Convert SQLite rows into plain dicts keyed by column names.

    Why: keeps the rest of the code independent of sqlite3.Row behavior and makes
    JSON serialization/tests straightforward.
    """
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def connect(db_path: str) -> sqlite3.Connection:
    """
    Open a SQLite connection with:
    - parent directory auto-created
    - foreign keys enforced
    - row_factory returning dicts
    """
    Path(os.path.dirname(db_path)).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = _dict_row_factory
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection, *, schema_path: str) -> None:
    """
    Initialize a DB by executing the schema.sql DDL.

    Notes:
    - This assumes schema.sql uses CREATE TABLE / CREATE INDEX (idempotent enough for dev).
    - For strict idempotency we'd add IF NOT EXISTS everywhere, but we keep schema aligned
      with docs/SCHEMA.md for clarity.
    """
    with open(schema_path, "r", encoding="utf-8") as f:
        ddl = f.read()
    conn.executescript(ddl)
    conn.commit()


def execute(conn: sqlite3.Connection, sql: str, params: Iterable[Any] | None = None) -> None:
    """
    Execute a statement and commit.
    """
    conn.execute(sql, tuple(params or ()))
    conn.commit()


def fetch_one(conn: sqlite3.Connection, sql: str, params: Iterable[Any] | None = None) -> dict[str, Any] | None:
    """
    Run a SELECT and return one row (dict) or None.
    """
    cur = conn.execute(sql, tuple(params or ()))
    row = cur.fetchone()
    return row if row is not None else None


def fetch_all(conn: sqlite3.Connection, sql: str, params: Iterable[Any] | None = None) -> list[dict[str, Any]]:
    """
    Run a SELECT and return all rows (list of dicts).
    """
    cur = conn.execute(sql, tuple(params or ()))
    return list(cur.fetchall())


@contextmanager
def db_conn(db_path: str) -> Iterator[sqlite3.Connection]:
    """
    Context manager for opening/closing connections.
    """
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()

