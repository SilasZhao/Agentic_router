from __future__ import annotations

"""Request-related domain tools.

Contracts: see `docs/TOOLS.md`.
Tools:
- `get_recent_requests`
- `get_request_detail`
"""

from datetime import timedelta
from typing import Any, Literal

from src.context._common import (
    assert_read_only_sql,
    db_now,
    default_db_path,
    error,
    parse_json_dict,
    parse_timeish,
    to_rfc3339_z,
)
from src.db.connection import db_conn, fetch_all, fetch_one


def get_recent_requests(
    *,
    user_id: str | None = None,
    user_tier: Literal["premium", "standard", "budget"] | None = None,
    deployment_id: str | None = None,
    model_id: str | None = None,
    backend_id: str | None = None,
    status: Literal["success", "error", "timeout"] | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Get recent requests with optional filters."""

    if user_tier is not None and user_tier not in ("premium", "standard", "budget"):
        return error(f"Invalid user_tier: {user_tier}", code="INVALID_PARAMETER")
    if status is not None and status not in ("success", "error", "timeout"):
        return error(f"Invalid status: {status}", code="INVALID_PARAMETER")
    if not isinstance(limit, int) or limit <= 0 or limit > 500:
        return error(f"Invalid limit: {limit}", code="INVALID_PARAMETER")

    db_path = db_path or default_db_path()

    try:
        with db_conn(db_path) as conn:
            now = db_now(conn)

            where: list[str] = []
            params: list[Any] = []

            if user_id:
                where.append("r.user_id = ?")
                params.append(user_id)
            if deployment_id:
                where.append("r.deployment_id = ?")
                params.append(deployment_id)
            if model_id:
                where.append("r.model_id = ?")
                params.append(model_id)
            if backend_id:
                where.append("r.backend_id = ?")
                params.append(backend_id)
            if status:
                where.append("r.status = ?")
                params.append(status)
            if user_tier:
                where.append("u.tier_id = ?")
                params.append(user_tier)

            # Time range defaults
            if since is None:
                since_dt = now - timedelta(hours=24)
            else:
                try:
                    since_dt = parse_timeish(since, now=now)
                except Exception as e:
                    return error(f"Invalid since: {e}", code="INVALID_PARAMETER")

            if until is None:
                until_dt = now
            else:
                try:
                    until_dt = parse_timeish(until, now=now)
                except Exception as e:
                    return error(f"Invalid until: {e}", code="INVALID_PARAMETER")

            where.append("r.created_at >= ?")
            params.append(to_rfc3339_z(since_dt))
            where.append("r.created_at <= ?")
            params.append(to_rfc3339_z(until_dt))

            where_sql = f"WHERE {' AND '.join(where)}" if where else ""

            # Fetch one extra row to compute has_more.
            sql = f"""
                SELECT
                    r.id,
                    r.user_id,
                    u.tier_id AS user_tier,
                    r.deployment_id,
                    r.model_id,
                    r.backend_id,
                    r.task_type,
                    r.latency_ms,
                    r.cost_usd,
                    r.status,
                    r.created_at
                FROM requests r
                JOIN users u ON u.id = r.user_id
                {where_sql}
                ORDER BY r.created_at DESC
                LIMIT ?;
            """.strip()
            assert_read_only_sql(sql)

            rows = fetch_all(conn, sql, params + [limit + 1])
            has_more = len(rows) > limit
            rows = rows[:limit]

            out = []
            for r in rows:
                out.append(
                    {
                        "id": r.get("id"),
                        "user_id": r.get("user_id"),
                        "user_tier": r.get("user_tier"),
                        "deployment_id": r.get("deployment_id"),
                        "model_id": r.get("model_id"),
                        "backend_id": r.get("backend_id"),
                        "task_type": r.get("task_type"),
                        "latency_ms": r.get("latency_ms"),
                        "cost_usd": r.get("cost_usd"),
                        "status": r.get("status"),
                        "created_at": r.get("created_at"),
                    }
                )

            return {"requests": out, "count": len(out), "has_more": has_more}

    except Exception as e:
        return error(f"Database error: {e}", code="DATABASE_ERROR")


def get_request_detail(*, request_id: str, db_path: str | None = None) -> dict[str, Any]:
    """Get full details of a single request including routing decision."""

    if not request_id:
        return error("request_id is required", code="INVALID_PARAMETER")

    db_path = db_path or default_db_path()

    try:
        with db_conn(db_path) as conn:
            sql = """
                SELECT
                    r.id,
                    r.user_id,
                    u.tier_id AS user_tier,
                    r.deployment_id,
                    r.model_id,
                    r.backend_id,
                    r.task_type,
                    r.input_tokens,
                    r.output_tokens,
                    r.cost_usd,
                    r.latency_ms,
                    r.status,
                    r.router_version,
                    r.experiment_id,
                    r.created_at,
                    r.routing_reason_json
                FROM requests r
                JOIN users u ON u.id = r.user_id
                WHERE r.id = ?
                LIMIT 1;
            """.strip()
            assert_read_only_sql(sql)
            row = fetch_one(conn, sql, [request_id])
            if not row:
                return error(f"Request not found: {request_id}", code="NOT_FOUND")

            routing_decision = parse_json_dict(row.get("routing_reason_json"))

            # Latest quality score (if present)
            q_sql = """
                SELECT score
                FROM quality_scores
                WHERE request_id = ?
                ORDER BY evaluated_at DESC
                LIMIT 1;
            """.strip()
            assert_read_only_sql(q_sql)
            q = fetch_one(conn, q_sql, [request_id])
            quality_score = q.get("score") if q else None

            # Related incident (best-effort): deployment match > model match > backend match.
            inc_sql = """
                SELECT id, target_type, target_id, title, status, started_at, resolved_at
                FROM incidents
                WHERE started_at <= ?
                  AND (resolved_at IS NULL OR resolved_at >= ?)
                  AND (
                    (target_type = 'deployment' AND target_id = ?)
                    OR (target_type = 'model' AND target_id = ?)
                    OR (target_type = 'backend' AND target_id = ?)
                  )
                ORDER BY
                  CASE
                    WHEN target_type = 'deployment' AND target_id = ? THEN 0
                    WHEN target_type = 'model' AND target_id = ? THEN 1
                    WHEN target_type = 'backend' AND target_id = ? THEN 2
                    ELSE 3
                  END,
                  started_at DESC
                LIMIT 1;
            """.strip()
            assert_read_only_sql(inc_sql)
            created_at = row.get("created_at")
            inc = None
            if isinstance(created_at, str) and created_at:
                inc = fetch_one(
                    conn,
                    inc_sql,
                    [
                        created_at,
                        created_at,
                        row.get("deployment_id"),
                        row.get("model_id"),
                        row.get("backend_id"),
                        row.get("deployment_id"),
                        row.get("model_id"),
                        row.get("backend_id"),
                    ],
                )

            request = {
                "id": row.get("id"),
                "user_id": row.get("user_id"),
                "user_tier": row.get("user_tier"),
                "deployment_id": row.get("deployment_id"),
                "model_id": row.get("model_id"),
                "backend_id": row.get("backend_id"),
                "task_type": row.get("task_type"),
                "input_tokens": row.get("input_tokens"),
                "output_tokens": row.get("output_tokens"),
                "cost_usd": row.get("cost_usd"),
                "latency_ms": row.get("latency_ms"),
                "status": row.get("status"),
                "router_version": row.get("router_version"),
                "experiment_id": row.get("experiment_id"),
                "created_at": row.get("created_at"),
                "routing_decision": routing_decision,
            }

            return {"request": request, "quality_score": quality_score, "related_incident": inc}

    except Exception as e:
        return error(f"Database error: {e}", code="DATABASE_ERROR")
