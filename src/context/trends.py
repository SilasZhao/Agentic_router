from __future__ import annotations

"""Trend/aggregate domain tools.

Contracts: see `docs/TOOLS.md`.
Tools:
- `get_latency_trends`
- `get_quality_summary`
- `get_request_volume`
"""

from typing import Any, Literal

from src.context._common import assert_read_only_sql, db_now, default_db_path, error, parse_rfc3339_z, parse_timeish, to_rfc3339_z
from src.db.connection import db_conn, fetch_all


def get_latency_trends(
    *,
    deployment_id: str | None = None,
    model_id: str | None = None,
    backend_id: str | None = None,
    since: str = "24 hours ago",
    until: str = "now",
    granularity: Literal["hour", "day"] = "hour",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Get latency metrics over time."""

    if granularity not in ("hour", "day"):
        return error(f"Invalid granularity: {granularity}", code="INVALID_PARAMETER")

    db_path = db_path or default_db_path()

    try:
        with db_conn(db_path) as conn:
            now = db_now(conn)
            try:
                since_dt = parse_timeish(since, now=now)
                until_dt = parse_timeish(until, now=now)
            except Exception as e:
                return error(f"Invalid time range: {e}", code="INVALID_PARAMETER")

            where: list[str] = []
            params: list[Any] = []
            if deployment_id:
                where.append("deployment_id = ?")
                params.append(deployment_id)
            if model_id:
                where.append("model_id = ?")
                params.append(model_id)
            if backend_id:
                where.append("backend_id = ?")
                params.append(backend_id)

            where.append("created_at >= ?")
            params.append(to_rfc3339_z(since_dt))
            where.append("created_at <= ?")
            params.append(to_rfc3339_z(until_dt))

            where_sql = f"WHERE {' AND '.join(where)}"

            sql = f"""
                SELECT created_at, deployment_id, latency_ms, status
                FROM requests
                {where_sql}
                ORDER BY created_at ASC;
            """.strip()
            assert_read_only_sql(sql)
            rows = fetch_all(conn, sql, params)

        # Group in Python to compute percentiles (SQLite has no built-in p50/p95).
        def bucket(ts: str) -> str:
            dt = parse_rfc3339_z(ts)
            if granularity == "hour":
                dt = dt.replace(minute=0, second=0, microsecond=0)
            else:
                dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            return to_rfc3339_z(dt)

        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for r in rows:
            ts = r.get("created_at")
            dep = r.get("deployment_id")
            if not isinstance(ts, str) or not ts or not isinstance(dep, str) or not dep:
                continue
            k = (bucket(ts), dep)
            groups.setdefault(k, []).append(r)

        def percentile_int(values: list[int], p: float) -> int | None:
            if not values:
                return None
            vs = sorted(values)
            idx = int((len(vs) - 1) * p)
            return int(vs[max(0, min(idx, len(vs) - 1))])

        data: list[dict[str, Any]] = []
        total_requests = 0
        weighted_p50_sum = 0.0
        weighted_p95_sum = 0.0

        for (period, dep), rs in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
            latencies = [int(v) for v in (r.get("latency_ms") for r in rs) if isinstance(v, int)]
            n = len(rs)
            if n == 0:
                continue
            p50 = percentile_int(latencies, 0.50)
            p95 = percentile_int(latencies, 0.95)
            errors = sum(1 for r in rs if r.get("status") in ("error", "timeout"))
            err_rate = errors / n if n else 0.0

            data.append(
                {
                    "period": period,
                    "deployment_id": dep,
                    "request_count": n,
                    "latency_p50_ms": p50,
                    "latency_p95_ms": p95,
                    "error_rate": err_rate,
                }
            )

            total_requests += n
            if p50 is not None:
                weighted_p50_sum += p50 * n
            if p95 is not None:
                weighted_p95_sum += p95 * n

        summary = {
            "total_requests": total_requests,
            "avg_latency_p50_ms": (weighted_p50_sum / total_requests) if total_requests else None,
            "avg_latency_p95_ms": (weighted_p95_sum / total_requests) if total_requests else None,
        }

        return {"data": data, "summary": summary}

    except Exception as e:
        return error(f"Database error: {e}", code="DATABASE_ERROR")


def get_quality_summary(
    *,
    model_id: str | None = None,
    task_type: str | None = None,
    since: str = "7 days ago",
    until: str = "now",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Get quality scores aggregated by model and task type."""

    db_path = db_path or default_db_path()

    try:
        with db_conn(db_path) as conn:
            now = db_now(conn)
            try:
                since_dt = parse_timeish(since, now=now)
                until_dt = parse_timeish(until, now=now)
            except Exception as e:
                return error(f"Invalid time range: {e}", code="INVALID_PARAMETER")

            where: list[str] = []
            params: list[Any] = []
            if model_id:
                where.append("r.model_id = ?")
                params.append(model_id)
            if task_type:
                where.append("r.task_type = ?")
                params.append(task_type)

            where.append("q.evaluated_at >= ?")
            params.append(to_rfc3339_z(since_dt))
            where.append("q.evaluated_at <= ?")
            params.append(to_rfc3339_z(until_dt))

            where_sql = f"WHERE {' AND '.join(where)}" if where else ""

            sql = f"""
                SELECT
                    r.model_id AS model_id,
                    COALESCE(r.task_type, 'unknown') AS task_type,
                    AVG(q.score) AS avg_score,
                    MIN(q.score) AS min_score,
                    MAX(q.score) AS max_score,
                    COUNT(*) AS sample_count
                FROM quality_scores q
                JOIN requests r ON r.id = q.request_id
                {where_sql}
                GROUP BY r.model_id, COALESCE(r.task_type, 'unknown')
                ORDER BY sample_count DESC, r.model_id ASC, task_type ASC;
            """.strip()
            assert_read_only_sql(sql)
            rows = fetch_all(conn, sql, params)

            data: list[dict[str, Any]] = []
            for r in rows:
                data.append(
                    {
                        "model_id": r.get("model_id"),
                        "task_type": r.get("task_type"),
                        "avg_score": r.get("avg_score"),
                        "min_score": r.get("min_score"),
                        "max_score": r.get("max_score"),
                        "sample_count": int(r.get("sample_count") or 0),
                    }
                )

            return {"data": data}

    except Exception as e:
        return error(f"Database error: {e}", code="DATABASE_ERROR")


def get_request_volume(
    *,
    group_by: Literal["tier", "model", "backend", "deployment"] = "tier",
    since: str = "7 days ago",
    until: str = "now",
    granularity: Literal["hour", "day"] = "day",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Get request counts over time, grouped by various dimensions."""

    if group_by not in ("tier", "model", "backend", "deployment"):
        return error(f"Invalid group_by: {group_by}", code="INVALID_PARAMETER")
    if granularity not in ("hour", "day"):
        return error(f"Invalid granularity: {granularity}", code="INVALID_PARAMETER")

    db_path = db_path or default_db_path()

    try:
        with db_conn(db_path) as conn:
            now = db_now(conn)
            try:
                since_dt = parse_timeish(since, now=now)
                until_dt = parse_timeish(until, now=now)
            except Exception as e:
                return error(f"Invalid time range: {e}", code="INVALID_PARAMETER")

            start_s = to_rfc3339_z(since_dt)
            end_s = to_rfc3339_z(until_dt)

            # Period formatting: day -> YYYY-MM-DD (docs example), hour -> RFC3339Z hour bucket.
            period_expr = "SUBSTR(r.created_at, 1, 10)" if granularity == "day" else "(SUBSTR(r.created_at, 1, 13) || ':00:00Z')"

            if group_by == "tier":
                group_expr = "u.tier_id"
                join_sql = "JOIN users u ON u.id = r.user_id"
            elif group_by == "model":
                group_expr = "r.model_id"
                join_sql = ""
            elif group_by == "backend":
                group_expr = "r.backend_id"
                join_sql = ""
            else:
                group_expr = "r.deployment_id"
                join_sql = ""

            sql = f"""
                SELECT
                    {period_expr} AS period,
                    {group_expr} AS grp,
                    COUNT(*) AS request_count,
                    COALESCE(SUM(r.cost_usd), 0) AS total_cost_usd
                FROM requests r
                {join_sql}
                WHERE r.created_at >= ?
                  AND r.created_at <= ?
                GROUP BY period, grp
                ORDER BY period ASC, grp ASC;
            """.strip()
            assert_read_only_sql(sql)
            rows = fetch_all(conn, sql, [start_s, end_s])

            data: list[dict[str, Any]] = []
            totals: dict[str, dict[str, Any]] = {}
            for r in rows:
                grp = r.get("grp")
                if grp is None:
                    continue
                grp_s = str(grp)
                rc = int(r.get("request_count") or 0)
                cost = float(r.get("total_cost_usd") or 0.0)
                data.append(
                    {
                        "period": r.get("period"),
                        "group": grp_s,
                        "request_count": rc,
                        "total_cost_usd": cost,
                    }
                )
                totals.setdefault(grp_s, {"requests": 0, "cost_usd": 0.0})
                totals[grp_s]["requests"] += rc
                totals[grp_s]["cost_usd"] += cost

            return {"data": data, "totals": totals}

    except Exception as e:
        return error(f"Database error: {e}", code="DATABASE_ERROR")
