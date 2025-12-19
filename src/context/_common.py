from __future__ import annotations

"""Shared utilities for context-layer tools.

Why this module exists:
- Keep domain tool modules small and focused
- Centralize read-only SQL guardrails
- Provide consistent error/time parsing helpers
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from src.db.connection import fetch_one


ErrorCode = Literal["NOT_FOUND", "INVALID_PARAMETER", "DATABASE_ERROR"]


def error(message: str, *, code: ErrorCode) -> dict[str, Any]:
    return {"error": True, "message": message, "code": code}


def assert_read_only_sql(sql: str) -> None:
    """Guardrail: all SQL executed by domain tools must be read-only."""
    s = (sql or "").strip()
    if not s:
        raise ValueError("Empty SQL")
    # Disallow multiple statements (best-effort).
    if ";" in s.rstrip(";"):
        raise ValueError("Multiple SQL statements are not allowed")
    s0 = s.lstrip("(").lstrip().lower()
    if not s0.startswith(("select", "with", "explain", "pragma")):
        raise ValueError("Non read-only SQL is not allowed")
    # Extra defense: reject common write keywords even if embedded.
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
    )
    if any(f" {kw} " in f" {s0} " for kw in blocked):
        raise ValueError("Write keyword detected in SQL")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_db_path() -> str:
    return os.getenv("CONTEXT_DB_PATH", str(project_root() / "data" / "context.db"))


def parse_rfc3339_z(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def parse_timeish(value: str, *, now: datetime) -> datetime:
    """Parse RFC3339Z or small set of relative strings used by tools."""
    v = (value or "").strip()
    if not v:
        raise ValueError("Empty time value")
    if v.endswith("Z") and "T" in v:
        return parse_rfc3339_z(v)

    s = v.lower()
    if s == "now":
        return now
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if s == "yesterday":
        start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start_today - timedelta(days=1)
    if s.endswith(" hour ago") or s.endswith(" hours ago"):
        n = int(s.split(" ", 1)[0])
        return now - timedelta(hours=n)
    if s.endswith(" day ago") or s.endswith(" days ago"):
        n = int(s.split(" ", 1)[0])
        return now - timedelta(days=n)

    raise ValueError(f"Unrecognized time format: {value}")


def to_rfc3339_z(dt: datetime) -> str:
    dt = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def db_now(conn) -> datetime:
    """Best-effort "now" anchored to DB timestamps (seeded DB is historical)."""

    sql = """
        WITH ts AS (
            SELECT MAX(updated_at) AS t FROM deployment_state_current
            UNION ALL SELECT MAX(created_at) FROM requests
            UNION ALL SELECT MAX(evaluated_at) FROM quality_scores
            UNION ALL SELECT MAX(started_at) FROM incidents
            UNION ALL SELECT MAX(resolved_at) FROM incidents
        )
        SELECT MAX(t) AS now_ts FROM ts;
    """.strip()
    assert_read_only_sql(sql)
    row = fetch_one(conn, sql)

    ts = (row or {}).get("now_ts")
    if isinstance(ts, str) and ts:
        try:
            return parse_rfc3339_z(ts)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def parse_json_dict(value: str | None) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        obj = json.loads(value)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None
