from __future__ import annotations

"""User-related domain tools.

Contracts: see `docs/TOOLS.md`.
Tools:
- `get_user_context`
"""

from datetime import timedelta
from typing import Any

from src.context._common import assert_read_only_sql, db_now, default_db_path, error, to_rfc3339_z
from src.db.connection import db_conn, fetch_one


def get_user_context(*, user_id: str, db_path: str | None = None) -> dict[str, Any]:
    """Get user info including current budget usage."""

    if not user_id:
        return error("user_id is required", code="INVALID_PARAMETER")

    db_path = db_path or default_db_path()

    try:
        with db_conn(db_path) as conn:
            now = db_now(conn)

            sql = """
                SELECT
                    u.id,
                    u.tier_id AS tier,
                    COALESCE(u.latency_sla_p95_ms_override, t.latency_sla_p95_ms) AS latency_sla_ms,
                    u.daily_budget_usd
                FROM users u
                JOIN tiers t ON t.id = u.tier_id
                WHERE u.id = ?
                LIMIT 1;
            """.strip()
            assert_read_only_sql(sql)
            u = fetch_one(conn, sql, [user_id])
            if not u:
                return error(f"User not found: {user_id}", code="NOT_FOUND")

            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            start_s = to_rfc3339_z(start)
            end_s = to_rfc3339_z(end)

            usage_sql = """
                SELECT
                    COALESCE(SUM(cost_usd), 0) AS daily_budget_used_usd,
                    COUNT(*) AS requests_today
                FROM requests
                WHERE user_id = ?
                  AND created_at >= ?
                  AND created_at < ?;
            """.strip()
            assert_read_only_sql(usage_sql)
            usage = fetch_one(conn, usage_sql, [user_id, start_s, end_s]) or {}

            used = float(usage.get("daily_budget_used_usd") or 0.0)
            requests_today = int(usage.get("requests_today") or 0)
            budget = u.get("daily_budget_usd")
            remaining: float | None = None
            if budget is not None:
                try:
                    remaining = float(budget) - used
                except Exception:
                    remaining = None

            return {
                "user": {
                    "id": u.get("id"),
                    "tier": u.get("tier"),
                    "latency_sla_ms": u.get("latency_sla_ms"),
                    "daily_budget_usd": u.get("daily_budget_usd"),
                    "daily_budget_used_usd": used,
                    "daily_budget_remaining_usd": remaining,
                    "requests_today": requests_today,
                }
            }

    except Exception as e:
        return error(f"Database error: {e}", code="DATABASE_ERROR")
