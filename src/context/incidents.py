from __future__ import annotations

"""Incident-related domain tools.

Contracts: see `docs/TOOLS.md`.
Tools:
- `get_active_incidents`
"""

from datetime import datetime
from typing import Any, Literal

from src.context._common import assert_read_only_sql, db_now, default_db_path, error, parse_rfc3339_z
from src.db.connection import db_conn, fetch_all


def get_active_incidents(
    *,
    target_type: Literal["deployment", "model", "backend"] | None = None,
    target_id: str | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Get currently active incidents."""

    if target_type is not None and target_type not in ("deployment", "model", "backend"):
        return error(f"Invalid target_type: {target_type}", code="INVALID_PARAMETER")

    db_path = db_path or default_db_path()

    try:
        with db_conn(db_path) as conn:
            now = db_now(conn)

            where = ["status = 'active'"]
            params: list[Any] = []
            if target_type:
                where.append("target_type = ?")
                params.append(target_type)
            if target_id:
                where.append("target_id = ?")
                params.append(target_id)
            where_sql = f"WHERE {' AND '.join(where)}"

            sql = f"""
                SELECT id, target_type, target_id, title, started_at
                FROM incidents
                {where_sql}
                ORDER BY started_at DESC;
            """.strip()
            assert_read_only_sql(sql)
            rows = fetch_all(conn, sql, params)

            incidents: list[dict[str, Any]] = []
            for r in rows:
                started_at = r.get("started_at")
                duration_minutes: int | None = None
                if isinstance(started_at, str) and started_at:
                    try:
                        duration_minutes = int((now - parse_rfc3339_z(started_at)).total_seconds() // 60)
                    except Exception:
                        duration_minutes = None

                incidents.append(
                    {
                        "id": r.get("id"),
                        "target_type": r.get("target_type"),
                        "target_id": r.get("target_id"),
                        "title": r.get("title"),
                        "started_at": started_at,
                        "duration_minutes": duration_minutes,
                    }
                )

            return {"incidents": incidents, "count": len(incidents)}

    except Exception as e:
        return error(f"Database error: {e}", code="DATABASE_ERROR")
