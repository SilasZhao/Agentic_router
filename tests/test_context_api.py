from __future__ import annotations

import os
import tempfile

from src.context.api import get_active_incidents, get_deployment_status, get_recent_requests, get_request_detail, get_user_context
from src.context.api import get_latency_trends
from src.context.api import get_quality_summary
from src.context.api import get_request_volume
from src.db.seed import SeedConfig, seed


def _seed_small_db() -> str:
    td = tempfile.TemporaryDirectory()
    db_path = os.path.join(td.name, "context.db")
    seed(db_path, cfg=SeedConfig(rng_seed=42, days=2, requests_per_user_per_day=10, write_report=False))
    # attach tempdir lifetime to path by stashing on function attribute
    _seed_small_db._td = td  # type: ignore[attr-defined]
    return db_path


def test_get_deployment_status_shape() -> None:
    db_path = _seed_small_db()
    out = get_deployment_status(db_path=db_path)
    assert "deployments" in out
    assert "summary" in out
    assert out["summary"]["total"] == 21
    assert isinstance(out["deployments"], list)
    assert out["deployments"], "expected non-empty deployments"
    d0 = out["deployments"][0]
    for k in (
        "id",
        "model_id",
        "backend_id",
        "status",
        "latency_p50_ms",
        "latency_p95_ms",
        "error_rate",
        "queue_depth",
        "cost_per_1k_tokens",
        "rate_limit_remaining",
        "sample_count",
        "updated_at",
        "is_stale",
    ):
        assert k in d0


def test_get_active_incidents_shape() -> None:
    db_path = _seed_small_db()
    out = get_active_incidents(db_path=db_path)
    assert "incidents" in out
    assert "count" in out
    assert isinstance(out["incidents"], list)
    if out["incidents"]:
        inc0 = out["incidents"][0]
        for k in ("id", "target_type", "target_id", "title", "started_at", "duration_minutes"):
            assert k in inc0


def test_get_recent_requests_limit_and_has_more() -> None:
    db_path = _seed_small_db()

    out = get_recent_requests(db_path=db_path, limit=5)
    assert "requests" in out and "count" in out and "has_more" in out
    assert out["count"] == 5
    assert len(out["requests"]) == 5
    assert out["has_more"] is True

    r0 = out["requests"][0]
    for k in (
        "id",
        "user_id",
        "user_tier",
        "deployment_id",
        "model_id",
        "backend_id",
        "task_type",
        "latency_ms",
        "cost_usd",
        "status",
        "created_at",
    ):
        assert k in r0


def test_get_request_detail_shape() -> None:
    db_path = _seed_small_db()
    recent = get_recent_requests(db_path=db_path, limit=1)
    req_id = recent["requests"][0]["id"]

    out = get_request_detail(db_path=db_path, request_id=req_id)
    assert "request" in out
    assert out["request"]["id"] == req_id
    assert "quality_score" in out
    assert "related_incident" in out

    r = out["request"]
    for k in (
        "id",
        "user_id",
        "user_tier",
        "deployment_id",
        "model_id",
        "backend_id",
        "task_type",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "latency_ms",
        "status",
        "router_version",
        "experiment_id",
        "created_at",
        "routing_decision",
    ):
        assert k in r


def test_get_user_context_shape() -> None:
    db_path = _seed_small_db()
    recent = get_recent_requests(db_path=db_path, limit=1)
    user_id = recent["requests"][0]["user_id"]

    out = get_user_context(db_path=db_path, user_id=user_id)
    assert "user" in out
    u = out["user"]
    for k in (
        "id",
        "tier",
        "latency_sla_ms",
        "daily_budget_usd",
        "daily_budget_used_usd",
        "daily_budget_remaining_usd",
        "requests_today",
    ):
        assert k in u
    assert u["id"] == user_id
    assert u["tier"] in ("premium", "standard", "budget")
    assert u["daily_budget_used_usd"] >= 0
    assert u["requests_today"] >= 0


def test_get_latency_trends_shape() -> None:
    db_path = _seed_small_db()
    out = get_latency_trends(db_path=db_path, since="2 days ago", until="now", granularity="day")
    assert "data" in out and "summary" in out
    assert isinstance(out["data"], list)
    assert "total_requests" in out["summary"]
    if out["data"]:
        d0 = out["data"][0]
        for k in ("period", "deployment_id", "request_count", "latency_p50_ms", "latency_p95_ms", "error_rate"):
            assert k in d0
        assert 0.0 <= d0["error_rate"] <= 1.0


def test_get_quality_summary_shape() -> None:
    db_path = _seed_small_db()
    out = get_quality_summary(db_path=db_path, since="7 days ago", until="now")
    assert "data" in out
    assert isinstance(out["data"], list)
    assert out["data"], "expected at least one quality summary row"
    d0 = out["data"][0]
    for k in ("model_id", "task_type", "avg_score", "min_score", "max_score", "sample_count"):
        assert k in d0
    assert d0["sample_count"] >= 0


def test_get_request_volume_shape() -> None:
    db_path = _seed_small_db()
    out = get_request_volume(db_path=db_path, group_by="tier", since="2 days ago", until="now", granularity="day")
    assert "data" in out and "totals" in out
    assert isinstance(out["data"], list)
    assert isinstance(out["totals"], dict)
    if out["data"]:
        d0 = out["data"][0]
        for k in ("period", "group", "request_count", "total_cost_usd"):
            assert k in d0
