from __future__ import annotations

"""Deployment-related domain tools.

Contracts: see `docs/TOOLS.md`.
Tools:
- `get_deployment_status`
"""

from datetime import datetime
from typing import Any, Literal

from src.context._common import assert_read_only_sql, db_now, default_db_path, error, parse_rfc3339_z
from src.db.connection import db_conn, fetch_all


def _is_stale(*, updated_at: str | None, sample_count: int | None, now: datetime) -> bool:
    # Heuristic from docs/SCHEMA.md: stale if old or too few samples.
    if sample_count is None or sample_count < 10:
        return True
    if not updated_at:
        return True
    try:
        age_sec = (now - parse_rfc3339_z(updated_at)).total_seconds()
    except Exception:
        return True
    return age_sec > 60


def get_deployment_status(
    *,
    model_id: str | None = None,
    backend_id: str | None = None,
    status: Literal["healthy", "degraded", "down"] | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Get current health status of deployments."""

    if status is not None and status not in ("healthy", "degraded", "down"):
        return error(f"Invalid status: {status}", code="INVALID_PARAMETER")

    db_path = db_path or default_db_path()

    try:
        with db_conn(db_path) as conn:
            now = db_now(conn)

            where: list[str] = []
            params: list[Any] = []
            if model_id:
                where.append("d.model_id = ?")
                params.append(model_id)
            if backend_id:
                where.append("d.backend_id = ?")
                params.append(backend_id)
            if status:
                where.append("COALESCE(s.status, 'down') = ?")
                params.append(status)

            where_sql = f"WHERE {' AND '.join(where)}" if where else ""

            sql = f"""
                SELECT
                    d.id AS id,
                    d.model_id AS model_id,
                    d.backend_id AS backend_id,
                    COALESCE(s.status, 'down') AS status,
                    s.latency_p50_ms AS latency_p50_ms,
                    s.latency_p95_ms AS latency_p95_ms,
                    s.error_rate AS error_rate,
                    s.queue_depth AS queue_depth,
                    NULL AS cost_per_1k_tokens,
                    s.rate_limit_remaining AS rate_limit_remaining,
                    s.sample_count AS sample_count,
                    s.updated_at AS updated_at
                FROM deployments d
                LEFT JOIN deployment_state_current s
                    ON s.deployment_id = d.id
                {where_sql}
                ORDER BY d.id;
            """.strip()
            assert_read_only_sql(sql)
            rows = fetch_all(conn, sql, params)

            deployments: list[dict[str, Any]] = []
            summary = {"total": 0, "healthy": 0, "degraded": 0, "down": 0}

            for r in rows:
                dep = {
                    "id": r.get("id"),
                    "model_id": r.get("model_id"),
                    "backend_id": r.get("backend_id"),
                    "status": r.get("status"),
                    "latency_p50_ms": r.get("latency_p50_ms"),
                    "latency_p95_ms": r.get("latency_p95_ms"),
                    "error_rate": r.get("error_rate"),
                    "queue_depth": r.get("queue_depth"),
                    "cost_per_1k_tokens": r.get("cost_per_1k_tokens"),
                    "rate_limit_remaining": r.get("rate_limit_remaining"),
                    "sample_count": r.get("sample_count"),
                    "updated_at": r.get("updated_at"),
                    "is_stale": _is_stale(updated_at=r.get("updated_at"), sample_count=r.get("sample_count"), now=now),
                }
                deployments.append(dep)

                summary["total"] += 1
                s = dep.get("status")
                if s in ("healthy", "degraded", "down"):
                    summary[s] += 1

            return {"deployments": deployments, "summary": summary}

    except Exception as e:
        return error(f"Database error: {e}", code="DATABASE_ERROR")
