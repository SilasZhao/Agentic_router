from __future__ import annotations

"""Safe ad-hoc SQL execution.

This is intentionally separate from domain tools:
- Domain tools use hardcoded SQL + `assert_read_only_sql`
- `safe_sql_query` accepts user-provided SQL but applies stricter guardrails

Guardrails:
- SELECT/WITH only (reject everything else)
- Single statement only
- Enforced max returned rows (default 100)
- Query timeout (default 5s) via SQLite progress handler
- Audit logging (JSONL)
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.context._common import default_db_path, error
from src.db.connection import db_conn, fetch_all


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_single_statement(sql: str) -> str:
    s = (sql or "").strip()
    if not s:
        raise ValueError("Empty SQL")

    # Strip a single trailing semicolon.
    if s.endswith(";"):
        s = s[:-1].rstrip()

    # Disallow any other semicolons (multiple statements).
    if ";" in s:
        raise ValueError("Multiple SQL statements are not allowed")

    return s


def _assert_select_only(sql: str) -> None:
    s0 = sql.lstrip("(").lstrip().lower()

    # Allow SELECT or WITH ... SELECT.
    if not (s0.startswith("select") or s0.startswith("with")):
        raise ValueError("Only SELECT/WITH queries are allowed")

    # Block obvious write keywords anywhere (best-effort).
    blocked = (
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "create",
        "replace",
        "truncate",
        "attach",
        "detach",
        "vacuum",
        "reindex",
    )
    if any(f" {kw} " in f" {s0} " for kw in blocked):
        raise ValueError("Write keyword detected in SQL")


def _wrap_with_limit(sql: str, *, fetch_limit: int) -> str:
    # Always wrap to guarantee LIMIT enforcement, even if user includes their own.
    return f"SELECT * FROM ({sql}) LIMIT {int(fetch_limit)}"


def _audit_path() -> Path:
    # Default: repo-local reports/ so it stays out of the DB.
    # Can be overridden for ops environments.
    p = Path(
        (  # noqa: W503
            __import__("os").getenv("SQL_AUDIT_LOG_PATH")
            or (Path(__file__).resolve().parents[2] / "reports" / "sql_audit.jsonl")
        )
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _append_audit(event: dict[str, Any]) -> None:
    try:
        path = _audit_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True, default=str) + "\n")
    except Exception:
        # Audit is best-effort; never fail the query solely due to logging.
        return


def safe_sql_query(
    *,
    query: str,
    params: list[Any] | None = None,
    db_path: str | None = None,
    max_rows: int = 100,
    timeout_sec: float = 5.0,
    audit: bool = True,
) -> dict[str, Any]:
    """Execute a guarded SELECT query against the context DB."""

    if not isinstance(max_rows, int) or max_rows <= 0 or max_rows > 1000:
        return error(f"Invalid max_rows: {max_rows}", code="INVALID_PARAMETER")
    if not isinstance(timeout_sec, (int, float)) or timeout_sec <= 0:
        return error(f"Invalid timeout_sec: {timeout_sec}", code="INVALID_PARAMETER")

    db_path = db_path or default_db_path()

    started_wall = time.perf_counter()
    event: dict[str, Any] = {
        "ts": _now_iso(),
        "db_path": str(db_path),
        "query": query,
        "params": params or [],
        "max_rows": max_rows,
        "timeout_sec": float(timeout_sec),
        "ok": False,
    }

    try:
        base = _normalize_single_statement(query)
        _assert_select_only(base)

        fetch_limit = max_rows + 1
        sql = _wrap_with_limit(base, fetch_limit=fetch_limit)
        event["executed_sql"] = sql

        with db_conn(db_path) as conn:
            # Timeout enforcement via progress handler.
            deadline = time.perf_counter() + float(timeout_sec)

            def _progress() -> int:
                return 1 if time.perf_counter() > deadline else 0

            conn.set_progress_handler(_progress, 1000)
            try:
                rows = fetch_all(conn, sql, list(params or []))
            finally:
                conn.set_progress_handler(None, 0)

        has_more = len(rows) > max_rows
        rows = rows[:max_rows]

        event["ok"] = True
        event["row_count"] = len(rows)
        event["has_more"] = has_more
        event["duration_ms"] = int((time.perf_counter() - started_wall) * 1000)
        if audit:
            _append_audit(event)

        return {"rows": rows, "row_count": len(rows), "has_more": has_more}

    except Exception as e:
        # SQLite uses an OperationalError with message "interrupted" on progress abort.
        msg = str(e)
        if "interrupted" in msg.lower():
            msg = f"Query timed out after {timeout_sec}s"

        event["error"] = msg
        event["duration_ms"] = int((time.perf_counter() - started_wall) * 1000)
        if audit:
            _append_audit(event)

        return error(f"Database error: {msg}", code="DATABASE_ERROR")
