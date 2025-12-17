from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

# Allow running this file directly (e.g. `python src/db/seed.py`) by ensuring the
# project root is on sys.path so `import src...` works.
if __package__ is None or __package__ == "":  # pragma: no cover
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from src.db.connection import connect, init_db


@dataclass(frozen=True)
class SeedConfig:
    """Deterministic seeding configuration for the V2 schema."""

    rng_seed: int = 42
    days: int = 15
    # Scale target: ~1k requests per user per day (~10k/day for 10 users).
    requests_per_user_per_day: int = 1000
    quality_coverage: float = 0.60
    base_now: datetime = datetime(2024, 1, 15, 16, 0, 0, tzinfo=timezone.utc)
    insert_batch_size: int = 5000
    # deployment_state_current will be computed from the last window_sec of requests.
    window_sec: int = 300
    # Reporting
    write_report: bool = True
    report_dir: str | None = None


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _schema_path() -> str:
    return os.path.join(_project_root(), "src", "db", "schema.sql")


def _default_db_path() -> str:
    return os.getenv("CONTEXT_DB_PATH", os.path.join(_project_root(), "data", "context.db"))


def to_rfc3339_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _reset_db(db_path: str) -> None:
    Path(os.path.dirname(db_path)).mkdir(parents=True, exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)


def seed(db_path: str | None = None, *, cfg: SeedConfig | None = None) -> str:
    """Create a fresh SQLite db and insert deterministic mock data (V2 schema)."""

    cfg = cfg or SeedConfig()
    db_path = db_path or _default_db_path()
    _reset_db(db_path)

    conn = connect(db_path)
    try:
        init_db(conn, schema_path=_schema_path())
        _seed_all(conn, cfg=cfg, db_path=db_path)
        conn.commit()
    finally:
        conn.close()

    return db_path


def _seed_all(conn: sqlite3.Connection, *, cfg: SeedConfig, db_path: str) -> None:
    rng = random.Random(cfg.rng_seed)
    window_start = cfg.base_now - timedelta(days=cfg.days)

    tiers = _make_tiers()
    models = _make_models()
    backends = _make_backends()
    deployments = _make_deployments(base_now=cfg.base_now)
    users = _make_users()
    incidents = _make_incidents(rng=rng, window_start=window_start, window_end=cfg.base_now)

    _insert_many(conn, "tiers", tiers, _insert_tiers)
    _insert_many(conn, "models", models, _insert_models)
    _insert_many(conn, "backends", backends, _insert_backends)
    _insert_many(conn, "deployments", deployments, _insert_deployments)
    _insert_many(conn, "users", users, _insert_users)
    _insert_many(conn, "incidents", incidents, _insert_incidents)

    perf_schedule = _make_daily_perf_schedule(rng=rng, deployments=deployments, days=cfg.days)
    req_gen = _generate_requests(
        rng=rng,
        users=users,
        deployments=deployments,
        window_start=window_start,
        window_end=cfg.base_now,
        requests_per_user_per_day=cfg.requests_per_user_per_day,
        incidents=incidents,
        perf_schedule=perf_schedule,
    )

    batch: list[dict] = []
    q_batch: list[dict] = []
    # Report aggregates (computed during generation)
    rep = _ReportBuilder(
        cfg=cfg,
        db_path=db_path,
        window_start=window_start,
        window_end=cfg.base_now,
        deployments=deployments,
        users=users,
        incidents=incidents,
        perf_schedule=perf_schedule,
    )
    for req in req_gen:
        batch.append(req)
        rep.add_request(req)
        if rng.random() <= cfg.quality_coverage:
            q = _make_quality_row(rng=rng, req=req, base_now=cfg.base_now, incidents=incidents)
            q_batch.append(q)
            rep.add_quality(q)

        if len(batch) >= cfg.insert_batch_size:
            # Important: insert request rows first, then their dependent quality rows.
            # This avoids FK failures when q_batch reaches the threshold before batch.
            _insert_requests(conn, batch)
            if q_batch:
                _insert_quality_scores(conn, q_batch)
            batch.clear()
            q_batch.clear()

    if batch:
        _insert_requests(conn, batch)
    if q_batch:
        # Remaining requests have been inserted above, so FK is satisfied.
        _insert_quality_scores(conn, q_batch)

    # Compute deployment_state_current from the last cfg.window_sec of requests.
    dep_state = _compute_deployment_state_from_recent_requests(
        conn=conn,
        deployments=deployments,
        incidents=incidents,
        window_end=cfg.base_now,
        window_sec=cfg.window_sec,
    )
    _insert_deployment_state_current(conn, dep_state)
    rep.set_deployment_state_current(dep_state)

    if cfg.write_report:
        report_dir = cfg.report_dir or os.path.join(_project_root(), "reports")
        rep.write(report_dir=report_dir)


def _insert_many(conn: sqlite3.Connection, name: str, rows: list[dict], inserter: Callable[[sqlite3.Connection, list[dict]], None]) -> None:
    if not rows:
        return
    inserter(conn, rows)


# -----------------------------
# Dimensions + SLA
# -----------------------------


def _make_tiers() -> list[dict]:
    return [
        {"id": "premium", "latency_sla_p95_ms": 500, "sla_window_sec": 300, "max_error_rate": 0.03, "max_timeout_rate": 0.02},
        {"id": "standard", "latency_sla_p95_ms": 900, "sla_window_sec": 300, "max_error_rate": 0.05, "max_timeout_rate": 0.03},
        {"id": "budget", "latency_sla_p95_ms": 1500, "sla_window_sec": 300, "max_error_rate": 0.08, "max_timeout_rate": 0.05},
    ]


def _make_models() -> list[dict]:
    # Exercise target: ~10 models (mix proprietary + OSS).
    return [
        {"id": "gpt-4", "provider": "openai", "max_context_tokens": 128000, "supports_streaming": 1, "supports_tools": 1, "supports_json_mode": 1, "notes": None},
        {"id": "gpt-4-mini", "provider": "openai", "max_context_tokens": 128000, "supports_streaming": 1, "supports_tools": 1, "supports_json_mode": 1, "notes": None},
        {"id": "gpt-3.5", "provider": "openai", "max_context_tokens": 16000, "supports_streaming": 1, "supports_tools": 1, "supports_json_mode": 1, "notes": None},
        {"id": "claude-3-opus", "provider": "anthropic", "max_context_tokens": 200000, "supports_streaming": 1, "supports_tools": 1, "supports_json_mode": 1, "notes": None},
        {"id": "claude-3-sonnet", "provider": "anthropic", "max_context_tokens": 200000, "supports_streaming": 1, "supports_tools": 1, "supports_json_mode": 1, "notes": None},
        {"id": "claude-3-haiku", "provider": "anthropic", "max_context_tokens": 200000, "supports_streaming": 1, "supports_tools": 1, "supports_json_mode": 1, "notes": None},
        {"id": "llama-70b", "provider": "self_hosted", "max_context_tokens": 8192, "supports_streaming": 1, "supports_tools": 0, "supports_json_mode": 0, "notes": None},
        {"id": "llama-13b", "provider": "self_hosted", "max_context_tokens": 8192, "supports_streaming": 1, "supports_tools": 0, "supports_json_mode": 0, "notes": None},
        {"id": "mistral-large", "provider": "self_hosted", "max_context_tokens": 32000, "supports_streaming": 1, "supports_tools": 0, "supports_json_mode": 0, "notes": None},
        {"id": "mixtral-8x7b", "provider": "self_hosted", "max_context_tokens": 32000, "supports_streaming": 1, "supports_tools": 0, "supports_json_mode": 0, "notes": None},
    ]


def _make_backends() -> list[dict]:
    return [
        {"id": "aws", "provider": "aws", "region": "us-east-1", "backend_type": "managed_api", "notes": None},
        {"id": "k8s", "provider": "kubernetes", "region": "us-east-1", "backend_type": "k8s_cluster", "notes": None},
        {"id": "neocloud", "provider": "neocloud", "region": "us-west-2", "backend_type": "gpu_fleet", "notes": None},
    ]


def _make_deployments(*, base_now: datetime) -> list[dict]:
    created_at = to_rfc3339_z(base_now - timedelta(days=30))
    # Keep a mix of deployments across 3 backends, with some redundancy/failover.
    # We intentionally keep one deployment disabled to represent an offline pool.
    defs: list[tuple[str, list[str]]] = [
        ("gpt-4", ["aws", "k8s", "neocloud"]),
        ("gpt-4-mini", ["aws", "k8s"]),
        ("gpt-3.5", ["aws", "k8s"]),
        ("claude-3-opus", ["aws", "neocloud"]),
        ("claude-3-sonnet", ["aws", "neocloud"]),
        ("claude-3-haiku", ["aws", "k8s"]),
        ("llama-70b", ["k8s", "neocloud"]),
        ("llama-13b", ["k8s", "neocloud"]),
        ("mistral-large", ["k8s", "neocloud"]),
        ("mixtral-8x7b", ["k8s", "neocloud"]),
    ]

    out: list[dict] = []
    for model_id, backends in defs:
        for backend_id in backends:
            dep_id = f"{model_id}/{backend_id}"
            enabled = 1
            weight = 1.0
            # One "down/disabled" example deployment.
            if dep_id == "llama-70b/neocloud":
                enabled = 0
                weight = 0.0
            out.append(
                {
                    "id": dep_id,
                    "model_id": model_id,
                    "backend_id": backend_id,
                    "enabled": enabled,
                    "weight": weight,
                    "created_at": created_at,
                }
            )
    return out


def _make_users() -> list[dict]:
    users: list[dict] = []
    for i in range(1, 4):
        users.append({"id": f"user_p_{i}", "tier_id": "premium", "daily_budget_usd": 50.0})
    for i in range(1, 5):
        users.append({"id": f"user_s_{i}", "tier_id": "standard", "daily_budget_usd": 20.0})
    for i in range(1, 4):
        users.append({"id": f"user_b_{i}", "tier_id": "budget", "daily_budget_usd": 5.0})
    return users


def _base_perf_primitives() -> dict[str, dict[str, float]]:
    # Deprecated: per-deployment base is now computed in _base_perf_for_deployment().
    return {}


def _base_perf_for_deployment(deployment_id: str) -> dict[str, float]:
    """
    Baseline TTFT + decode throughput for a deployment (before daily drift and incidents).
    This is intentionally synthetic and only needs to preserve relative ordering.
    """
    model_id, backend_id = deployment_id.split("/", 1)

    # Base per model (approximate relative performance).
    model_ttft_ms: dict[str, float] = {
        "gpt-4": 160.0,
        "gpt-4-mini": 120.0,
        "gpt-3.5": 110.0,
        "claude-3-opus": 150.0,
        "claude-3-sonnet": 130.0,
        "claude-3-haiku": 110.0,
        "llama-70b": 190.0,
        "llama-13b": 120.0,
        "mistral-large": 160.0,
        "mixtral-8x7b": 140.0,
    }
    model_decode_tps: dict[str, float] = {
        "gpt-4": 55.0,
        "gpt-4-mini": 75.0,
        "gpt-3.5": 85.0,
        "claude-3-opus": 60.0,
        "claude-3-sonnet": 70.0,
        "claude-3-haiku": 85.0,
        "llama-70b": 70.0,
        "llama-13b": 95.0,
        "mistral-large": 65.0,
        "mixtral-8x7b": 80.0,
    }

    # Backend multipliers: k8s tends to have higher TTFT (queueing) but decent throughput;
    # neocloud has slightly higher variance and sometimes slower decode.
    ttft_mult = {"aws": 1.0, "k8s": 1.35, "neocloud": 1.15}.get(backend_id, 1.2)
    decode_mult = {"aws": 1.0, "k8s": 0.95, "neocloud": 0.90}.get(backend_id, 0.92)

    # One intentionally "down-ish" pool baseline.
    if deployment_id == "llama-70b/neocloud":
        return {"ttft_ms": 9999.0, "decode_tps": 0.1}

    return {
        "ttft_ms": float(model_ttft_ms.get(model_id, 170.0) * ttft_mult),
        "decode_tps": max(5.0, float(model_decode_tps.get(model_id, 60.0) * decode_mult)),
    }


def _make_daily_perf_schedule(*, rng: random.Random, deployments: list[dict], days: int) -> dict[str, list[dict[str, float]]]:
    """
    Create a deterministic per-deployment daily schedule of *actual* performance primitives.

    Returns:
      {deployment_id: [{"ttft_ms": x, "decode_tps": y}, ...] } of length `days`.

    This intentionally makes the "baseline perf" evolve through time (once per day).
    """
    dep_ids = [d["id"] for d in deployments]
    days = max(int(days), 1)
    out: dict[str, list[dict[str, float]]] = {}

    for dep_id in dep_ids:
        ttft_mult = 1.0
        decode_mult = 1.0
        sched: list[dict[str, float]] = []

        for day in range(days):
            # Small daily random walk (bounded).
            ttft_mult *= 1.0 + rng.gauss(0.0, 0.04)
            decode_mult *= 1.0 + rng.gauss(0.0, 0.05)
            ttft_mult = min(1.6, max(0.7, ttft_mult))
            decode_mult = min(1.6, max(0.6, decode_mult))

            # Occasional step changes (e.g., deployment update / regression) about once every ~3 days.
            if rng.random() < 0.35:
                ttft_mult *= 1.0 + rng.gauss(0.0, 0.06)
                decode_mult *= 1.0 + rng.gauss(0.0, 0.07)
                ttft_mult = min(1.8, max(0.6, ttft_mult))
                decode_mult = min(1.8, max(0.5, decode_mult))

            b = _base_perf_for_deployment(dep_id)
            sched.append(
                {
                    "ttft_ms": float(b["ttft_ms"]) * float(ttft_mult),
                    "decode_tps": max(0.1, float(b["decode_tps"]) * float(decode_mult)),
                    "ttft_mult": float(ttft_mult),
                    "decode_mult": float(decode_mult),
                    "day_index": int(day),
                }
            )
        out[dep_id] = sched
    return out


# -----------------------------
# Incidents
# -----------------------------


def _make_incidents(*, rng: random.Random, window_start: datetime, window_end: datetime) -> list[dict]:
    """
    Generate multiple incidents across the full history window so different
    models/backends/deployments fail at different times.
    """
    # Fixed scenarios + a bit of deterministic randomness (via rng).
    # We keep the total count stable for tests/reporting.
    base_days = max(int((window_end - window_start).total_seconds() // 86400), 1)

    def at(day_offset: int, hour: int, minute: int = 0) -> datetime:
        return (window_start + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    incidents: list[dict] = []

    # Resolved incidents spread through time (varied scope).
    scenarios = [
        ("deployment", "gpt-4/k8s", "K8s node pressure: elevated latency + timeouts", 4, 10, 2.5),
        ("backend", "aws", "AWS us-east-1 elevated latency", 6, 16, 4.0),
        ("model", "gpt-4", "GPT-4 rate limiting spike", 8, 14, 3.0),
        ("deployment", "llama-70b/k8s", "GPU fragmentation: error rate increase", 10, 9, 5.0),
        ("backend", "neocloud", "Neocloud spot scarcity: slow queue drain", 12, 18, 6.0),
        ("deployment", "gpt-4/neocloud", "Cold starts: TTFT regression", 13, 11, 1.5),
        ("model", "claude-3-sonnet", "Claude Sonnet intermittent 5xx", 2, 13, 2.0),
    ]

    # If window is shorter than expected, clamp offsets.
    for idx, (tt, tid, title, day_off, hour, dur_h) in enumerate(scenarios, start=1):
        day_off = min(max(day_off, 0), base_days - 1)
        started = at(day_off, hour)
        resolved = started + timedelta(hours=dur_h)
        if resolved > window_end:
            resolved = window_end - timedelta(minutes=5)
        incidents.append(
            {
                "id": f"inc_{idx:03d}",
                "target_type": tt,
                "target_id": tid,
                "title": title,
                "status": "resolved",
                "started_at": to_rfc3339_z(started),
                "resolved_at": to_rfc3339_z(resolved),
            }
        )

    # One active incident near the end to exercise "active incident" flows.
    active_started = window_end - timedelta(hours=2)
    incidents.append(
        {
            "id": f"inc_{len(incidents)+1:03d}",
            "target_type": "deployment",
            "target_id": rng.choice(["gpt-4/aws", "gpt-4/k8s", "llama-70b/k8s"]),
            "title": "Active incident: elevated failures (simulated)",
            "status": "active",
            "started_at": to_rfc3339_z(active_started),
            "resolved_at": None,
        }
    )

    return incidents


def _parse_rfc3339_z(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _incident_effects_for_request(
    *,
    created_at: datetime,
    deployment_id: str,
    model_id: str,
    backend_id: str,
    incidents: list[dict],
) -> dict[str, float]:
    """
    Translate incidents into deterministic perturbations.

    Returns additive error/timeout deltas and multiplicative TTFT/decode factors.
    """
    err_add = 0.0
    timeout_add = 0.0
    ttft_mult = 1.0
    decode_mult = 1.0

    for inc in incidents:
        started = _parse_rfc3339_z(inc["started_at"])
        resolved_at = inc.get("resolved_at")
        ended = _parse_rfc3339_z(resolved_at) if resolved_at else None
        if created_at < started:
            continue
        if ended is not None and created_at > ended:
            continue

        target_type = inc["target_type"]
        target_id = inc["target_id"]
        match = (
            (target_type == "deployment" and target_id == deployment_id)
            or (target_type == "model" and target_id == model_id)
            or (target_type == "backend" and target_id == backend_id)
        )
        if not match:
            continue

        title = (inc.get("title") or "").lower()

        # Default: incidents make things worse.
        err_add += 0.03
        timeout_add += 0.02
        ttft_mult *= 1.15
        decode_mult *= 0.90

        # Latency-focused incident (TTFT regression).
        if "ttft" in title or "cold start" in title:
            ttft_mult *= 1.35
            decode_mult *= 0.97

        # Rate limiting causes more timeouts/errors but not necessarily slower decode.
        if "rate limit" in title:
            err_add += 0.05
            timeout_add += 0.05
            ttft_mult *= 1.05

        # Spot scarcity / queueing impacts TTFT and timeouts heavily.
        if "spot" in title or "queue" in title:
            timeout_add += 0.05
            ttft_mult *= 1.25
            decode_mult *= 0.85

        # Intermittent 5xx: mostly errors.
        if "5xx" in title:
            err_add += 0.07
            timeout_add += 0.01

    return {"error_add": err_add, "timeout_add": timeout_add, "ttft_mult": ttft_mult, "decode_mult": decode_mult}


def _weighted_choice(rng: random.Random, items: list[tuple[str, float]]) -> str:
    """
    Deterministic weighted choice using the provided rng.
    items: list of (value, weight) where weight >= 0
    """
    total = sum(w for _, w in items)
    if total <= 0:
        return items[0][0]
    r = rng.random() * total
    upto = 0.0
    for val, w in items:
        upto += w
        if r <= upto:
            return val
    return items[-1][0]


def _choose_deployment_for_request(
    *,
    rng: random.Random,
    tier_id: str,
    created_at: datetime,
    preferred: list[str],
    incidents: list[dict],
    enabled_ids: set[str],
) -> str:
    """
    Choose a deployment, reacting to incidents by reducing probability of affected targets.
    Still allows minor mistakes by sometimes ignoring incident penalties.
    """
    # Base preference weights by rank.
    base_rank_weights = [1.0, 0.6, 0.3]

    # Tier bias (kept from earlier version, but now incident-aware).
    if tier_id == "premium":
        base_rank_weights = [1.4, 0.7, 0.35]
    elif tier_id == "budget":
        base_rank_weights = [1.5, 0.55, 0.25]

    # Occasionally make a "mistake": ignore incident penalties and pick mostly by tier preference.
    if rng.random() < 0.04:
        return _weighted_choice(rng, [(dep, base_rank_weights[min(i, len(base_rank_weights) - 1)]) for i, dep in enumerate(preferred)])

    weighted: list[tuple[str, float]] = []
    for i, dep in enumerate(preferred):
        w = base_rank_weights[min(i, len(base_rank_weights) - 1)]
        model_id, backend_id = dep.split("/", 1)
        eff = _incident_effects_for_request(
            created_at=created_at,
            deployment_id=dep,
            model_id=model_id,
            backend_id=backend_id,
            incidents=incidents,
        )
        pressure = float(eff["error_add"]) + float(eff["timeout_add"])

        # Penalize deployments with incident pressure (deployment/model/backend scoped incidents).
        # Keep a non-zero floor to allow mistakes / lingering traffic.
        incident_penalty = max(0.15, 1.0 - 3.0 * pressure)
        w *= incident_penalty

        # Penalize disabled deployments heavily, but do not fully eliminate (minor mistakes).
        if dep not in enabled_ids:
            w *= 0.08

        # If incident makes TTFT much worse or decode much worse, apply an extra penalty.
        if float(eff["ttft_mult"]) >= 1.35:
            w *= 0.75
        if float(eff["decode_mult"]) <= 0.85:
            w *= 0.75

        weighted.append((dep, max(0.01, float(w))))

    return _weighted_choice(rng, weighted)


# -----------------------------
# Inserts
# -----------------------------


def _insert_tiers(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO tiers (id, latency_sla_p95_ms, sla_window_sec, max_error_rate, max_timeout_rate)
        VALUES (:id, :latency_sla_p95_ms, :sla_window_sec, :max_error_rate, :max_timeout_rate)
        """,
        rows,
    )


def _insert_models(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO models (id, provider, max_context_tokens, supports_streaming, supports_tools, supports_json_mode, notes)
        VALUES (:id, :provider, :max_context_tokens, :supports_streaming, :supports_tools, :supports_json_mode, :notes)
        """,
        rows,
    )


def _insert_backends(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO backends (id, provider, region, backend_type, notes)
        VALUES (:id, :provider, :region, :backend_type, :notes)
        """,
        rows,
    )


def _insert_deployments(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO deployments (id, model_id, backend_id, enabled, weight, created_at)
        VALUES (:id, :model_id, :backend_id, :enabled, :weight, :created_at)
        """,
        rows,
    )


def _insert_deployment_state_current(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO deployment_state_current (
          deployment_id, status, window_sec, sample_count, updated_at,
          latency_p50_ms, latency_p95_ms, error_rate, timeout_rate,
          queue_depth, rate_limit_remaining,
          ttft_p50_ms, ttft_p95_ms, decode_toks_per_sec_p50, decode_toks_per_sec_p95
        ) VALUES (
          :deployment_id, :status, :window_sec, :sample_count, :updated_at,
          :latency_p50_ms, :latency_p95_ms, :error_rate, :timeout_rate,
          :queue_depth, :rate_limit_remaining,
          :ttft_p50_ms, :ttft_p95_ms, :decode_toks_per_sec_p50, :decode_toks_per_sec_p95
        )
        """,
        rows,
    )


def _insert_users(conn: sqlite3.Connection, rows: list[dict]) -> None:
    # Keep seeding simple: no per-user overrides and no preferences for now.
    conn.executemany(
        """
        INSERT INTO users (
          id, tier_id, daily_budget_usd,
          latency_sla_p95_ms_override, max_error_rate_override, max_timeout_rate_override,
          preferences_json
        ) VALUES (
          :id, :tier_id, :daily_budget_usd,
          NULL, NULL, NULL,
          NULL
        )
        """,
        rows,
    )


def _insert_incidents(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO incidents (id, target_type, target_id, title, status, started_at, resolved_at)
        VALUES (:id, :target_type, :target_id, :title, :status, :started_at, :resolved_at)
        """,
        rows,
    )


def _insert_requests(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO requests (
          id, created_at, user_id, deployment_id,
          model_id, backend_id,
          task_type, input_tokens, output_tokens,
          latency_ms, ttft_ms, decode_toks_per_sec,
          cost_usd, status, error_code,
          router_version, experiment_id, routing_reason_json
        ) VALUES (
          :id, :created_at, :user_id, :deployment_id,
          :model_id, :backend_id,
          :task_type, :input_tokens, :output_tokens,
          :latency_ms, :ttft_ms, :decode_toks_per_sec,
          :cost_usd, :status, :error_code,
          :router_version, :experiment_id, :routing_reason_json
        )
        """,
        rows,
    )


def _insert_quality_scores(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO quality_scores (request_id, eval_type, score, evaluated_at) VALUES (:request_id, :eval_type, :score, :evaluated_at)",
        rows,
    )


# -----------------------------
# Request/quality generation
# -----------------------------


def _deployment_pricing_per_1k_tokens() -> dict[str, float]:
    # Deprecated (kept for backward compatibility in older notebooks/scripts).
    # Prefer _price_per_1k_tokens(deployment_id).
    return {}


def _price_per_1k_tokens(deployment_id: str) -> float:
    """
    Synthetic pricing model ($ per 1k total tokens).
    We intentionally do not store this in the schema; it is used to populate requests.cost_usd.
    """
    model_id, backend_id = deployment_id.split("/", 1)

    # Base model prices (rough ordering; not real-world accurate).
    model_price: dict[str, float] = {
        "gpt-4": 0.030,
        "gpt-4-mini": 0.010,
        "gpt-3.5": 0.004,
        "claude-3-opus": 0.025,
        "claude-3-sonnet": 0.012,
        "claude-3-haiku": 0.006,
        "llama-70b": 0.008,
        "llama-13b": 0.003,
        "mistral-large": 0.007,
        "mixtral-8x7b": 0.005,
    }

    # Backend multipliers: managed APIs more expensive than self-hosted.
    backend_mult: dict[str, float] = {"aws": 1.0, "k8s": 0.75, "neocloud": 0.65}

    return float(model_price.get(model_id, 0.008) * backend_mult.get(backend_id, 0.8))


def _tier_preferred_deployments(tier_id: str) -> list[str]:
    if tier_id == "premium":
        return ["gpt-4/aws", "claude-3-opus/aws", "gpt-4/neocloud"]
    if tier_id == "standard":
        return ["gpt-4-mini/aws", "claude-3-sonnet/aws", "mixtral-8x7b/k8s"]
    # Budget: mostly OSS/self-hosted, but include a cheaper Claude tier for coverage.
    return ["llama-13b/k8s", "mixtral-8x7b/k8s", "gpt-3.5/k8s", "claude-3-haiku/k8s"]


def _router_version_for(ts: datetime, *, window_start: datetime, window_end: datetime) -> str:
    midpoint = window_start + (window_end - window_start) / 2
    return "v1.1.0" if ts < midpoint else "v1.2.0"


def _experiment_id_for(rng: random.Random, ts: datetime, *, window_end: datetime) -> str | None:
    if ts >= (window_end - timedelta(days=3)) and rng.random() < 0.25:
        return "exp_latency_tuning"
    return None


def _generate_requests(
    *,
    rng: random.Random,
    users: list[dict],
    deployments: list[dict],
    window_start: datetime,
    window_end: datetime,
    requests_per_user_per_day: int,
    incidents: list[dict],
    perf_schedule: dict[str, list[dict[str, float]]],
) -> Iterator[dict]:
    enabled_ids = {d["id"] for d in deployments if int(d.get("enabled", 1)) == 1}
    task_types = ["summarization", "coding", "chat", "reasoning"]
    dep_ids = {d["id"] for d in deployments}

    total_days = max(int((window_end - window_start).total_seconds() // 86400), 1)
    per_user_total = requests_per_user_per_day * total_days

    # Deterministic iteration order: users list order, then sequential id per user.
    for user in users:
        tier_id = user["tier_id"]
        # Keep only preferences that exist in this seeded deployment set.
        preferred = [d for d in _tier_preferred_deployments(tier_id) if d in dep_ids]
        if not preferred:
            # Fallback: any enabled deployment for this tier.
            preferred = sorted(enabled_ids)[:3]

        for j in range(per_user_total):
            frac = (j + rng.random()) / max(per_user_total, 1)
            created_at_dt = window_start + (window_end - window_start) * frac
            day_index = int((created_at_dt - window_start).total_seconds() // 86400)
            if day_index < 0:
                day_index = 0
            if day_index >= total_days:
                day_index = total_days - 1

            # Choose deployment (tier-biased) and react to incidents.
            deployment_id = _choose_deployment_for_request(
                rng=rng,
                tier_id=tier_id,
                created_at=created_at_dt,
                preferred=preferred,
                incidents=incidents,
                enabled_ids=enabled_ids,
            )

            input_tokens = int(rng.triangular(200, 3000, 1200))
            output_tokens = int(rng.triangular(50, 1200, 300))
            task_type = rng.choice(task_types)

            model_id, backend_id = deployment_id.split("/", 1)

            # Failure rates (deployment baseline + incident overlays)
            base_error = 0.02
            base_timeout = 0.01
            if deployment_id == "gpt-4/k8s":
                base_error = 0.05
                base_timeout = 0.03

            eff = _incident_effects_for_request(
                created_at=created_at_dt,
                deployment_id=deployment_id,
                model_id=model_id,
                backend_id=backend_id,
                incidents=incidents,
            )
            base_error = min(0.95, max(0.0, base_error + float(eff["error_add"])))
            base_timeout = min(0.95, max(0.0, base_timeout + float(eff["timeout_add"])))

            roll = rng.random()
            if roll < base_timeout:
                status = "timeout"
            elif roll < (base_timeout + base_error):
                status = "error"
            else:
                status = "success"

            # Latency primitives with daily drift (once per day) plus incident overlays.
            daily = perf_schedule.get(deployment_id, [{"ttft_ms": 200.0, "decode_tps": 45.0}])[day_index]
            ttft_base = float(daily["ttft_ms"]) * float(eff["ttft_mult"])
            decode_base = float(daily["decode_tps"]) * float(eff["decode_mult"])

            ttft_ms = max(20, int(rng.gauss(ttft_base, ttft_base * 0.25)))
            decode_tps = max(5.0, float(rng.gauss(decode_base, decode_base * 0.20)))

            if status == "timeout":
                latency_ms = int(rng.triangular(1200, 4000, 2200))
            else:
                decode_ms = int((output_tokens / decode_tps) * 1000.0)
                noise = int(rng.gauss(0, 60))
                latency_ms = max(30, ttft_ms + decode_ms + noise)

            router_version = _router_version_for(created_at_dt, window_start=window_start, window_end=window_end)
            experiment_id = _experiment_id_for(rng, created_at_dt, window_end=window_end)

            cost_usd = round(((input_tokens + output_tokens) / 1000.0) * _price_per_1k_tokens(deployment_id), 6)

            routing_reason = {
                "tier_id": tier_id,
                "options_considered": [
                    {"deployment": preferred[0], "available": preferred[0] in dep_ids},
                    {"deployment": preferred[-1], "available": preferred[-1] in dep_ids},
                ],
                "decision": f"{deployment_id}: tier preference and health constraints",
            }

            req = {
                "id": f"req_{user['id']}_{j:06d}",
                "created_at": to_rfc3339_z(created_at_dt),
                "user_id": user["id"],
                "deployment_id": deployment_id,
                "model_id": model_id,
                "backend_id": backend_id,
                "task_type": task_type,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "ttft_ms": ttft_ms,
                "decode_toks_per_sec": round(float(decode_tps), 3),
                "cost_usd": cost_usd,
                "status": status,
                "error_code": None,
                "router_version": router_version,
                "experiment_id": experiment_id,
                "routing_reason_json": json.dumps(routing_reason),
            }

            yield req


def _make_quality_row(*, rng: random.Random, req: dict, base_now: datetime, incidents: list[dict]) -> dict:
    created = datetime.fromisoformat(req["created_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
    evaluated_at = created + timedelta(hours=int(rng.triangular(1, 8, 3)))
    if evaluated_at > base_now + timedelta(hours=1):
        evaluated_at = base_now + timedelta(minutes=int(rng.triangular(5, 55, 20)))

    eff = _incident_effects_for_request(
        created_at=created,
        deployment_id=req["deployment_id"],
        model_id=req["model_id"],
        backend_id=req["backend_id"],
        incidents=incidents,
    )
    in_incident = (eff["error_add"] + eff["timeout_add"]) > 0.0
    score = rng.triangular(0.35, 0.75, 0.55) if in_incident else rng.triangular(0.70, 0.98, 0.88)
    return {
        "request_id": req["id"],
        "eval_type": "offline",
        "score": round(float(score), 4),
        "evaluated_at": to_rfc3339_z(evaluated_at),
    }


def _percentile_int(vals: list[int], q: float) -> int | None:
    if not vals:
        return None
    vals_sorted = sorted(vals)
    if len(vals_sorted) == 1:
        return int(vals_sorted[0])
    q = max(0.0, min(1.0, float(q)))
    idx = int(q * (len(vals_sorted) - 1))
    return int(vals_sorted[idx])


def _percentile_float(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    vals_sorted = sorted(vals)
    if len(vals_sorted) == 1:
        return float(vals_sorted[0])
    q = max(0.0, min(1.0, float(q)))
    idx = int(q * (len(vals_sorted) - 1))
    return float(vals_sorted[idx])


def _compute_deployment_state_from_recent_requests(
    *,
    conn: sqlite3.Connection,
    deployments: list[dict],
    incidents: list[dict],
    window_end: datetime,
    window_sec: int,
) -> list[dict]:
    """
    Compute deployment_state_current snapshot from requests in the last window_sec.

    - Uses request logs (events) as source of truth.
    - Percentiles computed over successful requests only.
    - error_rate/timeout_rate computed over all requests in window.
    """
    window_sec = max(int(window_sec), 1)
    end_s = to_rfc3339_z(window_end)
    start_s = to_rfc3339_z(window_end - timedelta(seconds=window_sec))

    dep_rows = conn.execute(
        """
        SELECT deployment_id, status, latency_ms, ttft_ms, decode_toks_per_sec
        FROM requests
        WHERE created_at >= ? AND created_at <= ?
        """,
        (start_s, end_s),
    ).fetchall()

    # Aggregate per deployment
    agg: dict[str, dict[str, object]] = {}
    for d in deployments:
        agg[d["id"]] = {
            "total": 0,
            "errors": 0,
            "timeouts": 0,
            "lat": [],
            "ttft": [],
            "dec": [],
        }

    for r in dep_rows:
        dep_id = r["deployment_id"]
        if dep_id not in agg:
            continue
        a = agg[dep_id]
        a["total"] = int(a["total"]) + 1
        st = r["status"]
        if st == "error":
            a["errors"] = int(a["errors"]) + 1
        elif st == "timeout":
            a["timeouts"] = int(a["timeouts"]) + 1
        elif st == "success":
            if r["latency_ms"] is not None:
                a["lat"].append(int(r["latency_ms"]))
            if r["ttft_ms"] is not None:
                a["ttft"].append(int(r["ttft_ms"]))
            if r["decode_toks_per_sec"] is not None:
                a["dec"].append(float(r["decode_toks_per_sec"]))

    updated_at = end_s
    out: list[dict] = []
    for dep_id, a in agg.items():
        total = int(a["total"])
        errors = int(a["errors"])
        timeouts = int(a["timeouts"])
        error_rate = (errors / total) if total else 0.0
        timeout_rate = (timeouts / total) if total else 0.0

        lat: list[int] = a["lat"]
        ttft: list[int] = a["ttft"]
        dec: list[float] = a["dec"]

        # Status heuristic derived from window + active incidents overlapping window_end.
        status = "healthy"

        # If there is an active deployment incident for this deployment at window_end, mark it down.
        effects_now = _incident_effects_for_request(
            created_at=window_end,
            deployment_id=dep_id,
            model_id=dep_id.split("/", 1)[0],
            backend_id=dep_id.split("/", 1)[1],
            incidents=incidents,
        )
        active_pressure = effects_now["error_add"] + effects_now["timeout_add"]

        if active_pressure >= 0.15:
            status = "down"
        elif total >= 30 and (timeout_rate > 0.05 or error_rate > 0.07):
            status = "degraded"
        elif total == 0:
            # No recent samples; treat as healthy but low-confidence (sample_count=0).
            status = "healthy"

        # Synthetic infra metrics (queue depth + rate limit remaining).
        # These are intentionally simple and depend on recent load + status.
        _, backend_id = dep_id.split("/", 1)
        base_queue = {"aws": 12, "k8s": 40, "neocloud": 25}.get(backend_id, 20)
        base_rl = {"aws": 900, "k8s": 2000, "neocloud": 1200}.get(backend_id, 1000)
        queue_depth = None
        rate_limit_remaining = None
        if status == "down":
            queue_depth = None
            rate_limit_remaining = 0
        else:
            # Queue grows with recent traffic and timeout pressure.
            queue_depth = int(max(0, min(250, base_queue + (total * 2) + int(timeout_rate * 200))))
            # Remaining decreases with recent requests and errors/timeouts.
            burn = (total * 8) + (errors * 20) + (timeouts * 30)
            rate_limit_remaining = int(max(0, base_rl - burn))

        out.append(
            {
                "deployment_id": dep_id,
                "status": status,
                "window_sec": window_sec,
                "sample_count": total,
                "updated_at": updated_at,
                "latency_p50_ms": _percentile_int(lat, 0.50),
                "latency_p95_ms": _percentile_int(lat, 0.95),
                "error_rate": float(round(error_rate, 6)),
                "timeout_rate": float(round(timeout_rate, 6)),
                "queue_depth": queue_depth,
                "rate_limit_remaining": rate_limit_remaining,
                "ttft_p50_ms": _percentile_int(ttft, 0.50),
                "ttft_p95_ms": _percentile_int(ttft, 0.95),
                "decode_toks_per_sec_p50": _percentile_float(dec, 0.50),
                "decode_toks_per_sec_p95": _percentile_float(dec, 0.95),
            }
        )
    return out


class _ReportBuilder:
    def __init__(
        self,
        *,
        cfg: SeedConfig,
        db_path: str,
        window_start: datetime,
        window_end: datetime,
        deployments: list[dict],
        users: list[dict],
        incidents: list[dict],
        perf_schedule: dict[str, list[dict[str, float]]],
    ) -> None:
        self.cfg = cfg
        self.db_path = db_path
        self.window_start = window_start
        self.window_end = window_end
        self.deployments = deployments
        self.users = users
        self.incidents = incidents
        self.perf_schedule = perf_schedule

        self.request_count = 0
        self.quality_count = 0

        # daily metrics keyed by (day_iso, deployment_id)
        self.daily: dict[tuple[str, str], dict[str, object]] = {}
        self.dep_state_current: list[dict] | None = None

    def _get_bucket(self, day_iso: str, dep_id: str) -> dict[str, object]:
        key = (day_iso, dep_id)
        if key not in self.daily:
            self.daily[key] = {
                "total": 0,
                "success": 0,
                "error": 0,
                "timeout": 0,
                "lat": [],
                "ttft": [],
                "dec": [],
                "cost": 0.0,
            }
        return self.daily[key]

    def add_request(self, req: dict) -> None:
        self.request_count += 1
        created = datetime.fromisoformat(req["created_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
        day_iso = created.date().isoformat()
        dep_id = req["deployment_id"]
        b = self._get_bucket(day_iso, dep_id)
        b["total"] = int(b["total"]) + 1
        st = req["status"]
        if st == "success":
            b["success"] = int(b["success"]) + 1
            if req.get("latency_ms") is not None:
                b["lat"].append(int(req["latency_ms"]))
            if req.get("ttft_ms") is not None:
                b["ttft"].append(int(req["ttft_ms"]))
            if req.get("decode_toks_per_sec") is not None:
                b["dec"].append(float(req["decode_toks_per_sec"]))
        elif st == "error":
            b["error"] = int(b["error"]) + 1
        elif st == "timeout":
            b["timeout"] = int(b["timeout"]) + 1
        if req.get("cost_usd") is not None:
            b["cost"] = float(b["cost"]) + float(req["cost_usd"])

    def add_quality(self, q: dict) -> None:
        self.quality_count += 1

    def set_deployment_state_current(self, rows: list[dict]) -> None:
        self.dep_state_current = rows

    def write(self, *, report_dir: str) -> str:
        # report subdir keyed by timestamp (cfg.base_now is fixed; include rng_seed to avoid collisions)
        ts = self.window_end.strftime("%Y%m%dT%H%M%SZ")
        out_dir = os.path.join(report_dir, f"seed_{ts}_seed{self.cfg.rng_seed}")
        Path(out_dir).mkdir(parents=True, exist_ok=True)

        # Build daily summary table
        daily_rows: list[dict[str, object]] = []
        for (day_iso, dep_id), b in sorted(self.daily.items()):
            total = int(b["total"])
            success = int(b["success"])
            err = int(b["error"])
            to = int(b["timeout"])
            error_rate = (err / total) if total else 0.0
            timeout_rate = (to / total) if total else 0.0

            lat = b["lat"]
            ttft = b["ttft"]
            dec = b["dec"]

            daily_rows.append(
                {
                    "day": day_iso,
                    "deployment_id": dep_id,
                    "total": total,
                    "success": success,
                    "error": err,
                    "timeout": to,
                    "error_rate": round(error_rate, 6),
                    "timeout_rate": round(timeout_rate, 6),
                    "latency_p50_ms": _percentile_int(lat, 0.50),
                    "latency_p95_ms": _percentile_int(lat, 0.95),
                    "ttft_p50_ms": _percentile_int(ttft, 0.50),
                    "ttft_p95_ms": _percentile_int(ttft, 0.95),
                    "decode_tps_p50": _percentile_float(dec, 0.50),
                    "decode_tps_p95": _percentile_float(dec, 0.95),
                    "total_cost_usd": round(float(b["cost"]), 6),
                }
            )

        # Write machine-readable JSON
        meta = {
            "db_path": self.db_path,
            "config": {
                "rng_seed": self.cfg.rng_seed,
                "days": self.cfg.days,
                "requests_per_user_per_day": self.cfg.requests_per_user_per_day,
                "quality_coverage": self.cfg.quality_coverage,
                "base_now": to_rfc3339_z(self.cfg.base_now),
                "window_sec": self.cfg.window_sec,
            },
            "counts": {
                "users": len(self.users),
                "deployments": len(self.deployments),
                "requests": self.request_count,
                "quality_scores": self.quality_count,
                "incidents": len(self.incidents),
            },
            "incidents": self.incidents,
            "deployments": self.deployments,
            "perf_schedule": self.perf_schedule,
            "deployment_state_current": self.dep_state_current,
            "daily_metrics": daily_rows,
        }

        json_path = os.path.join(out_dir, "report.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, sort_keys=True)

        # Write a readable markdown report
        md_path = os.path.join(out_dir, "report.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Seed report\n\n")
            f.write(f"- DB: `{self.db_path}`\n")
            f.write(f"- Window: `{to_rfc3339_z(self.window_start)}` → `{to_rfc3339_z(self.window_end)}`\n")
            f.write(f"- Requests/user/day: **{self.cfg.requests_per_user_per_day}**\n")
            f.write(f"- Days: **{self.cfg.days}**\n")
            f.write(f"- Total requests: **{self.request_count}**\n")
            f.write(f"- Quality coverage: **{self.cfg.quality_coverage}** (actual rows: {self.quality_count})\n")
            f.write(f"- Snapshot computed from last **{self.cfg.window_sec}s** of requests\n\n")

            f.write("## Table counts\n\n")
            f.write(f"- tiers: 3\n")
            f.write(f"- models: 3\n")
            f.write(f"- backends: 3\n")
            f.write(f"- deployments: {len(self.deployments)}\n")
            f.write(f"- users: {len(self.users)}\n")
            f.write(f"- incidents: {len(self.incidents)}\n")
            f.write(f"- requests: {self.request_count}\n")
            f.write(f"- quality_scores: {self.quality_count}\n\n")

            f.write("## Current snapshot (deployment_state_current)\n\n")
            if self.dep_state_current:
                for r in sorted(self.dep_state_current, key=lambda x: x["deployment_id"]):
                    f.write(
                        f"- `{r['deployment_id']}` status={r['status']} samples={r['sample_count']} "
                        f"p50={r['latency_p50_ms']}ms p95={r['latency_p95_ms']}ms "
                        f"err={r['error_rate']} timeout={r['timeout_rate']} "
                        f"ttft_p50={r['ttft_p50_ms']} decode_p50={r['decode_toks_per_sec_p50']}\n"
                    )
            f.write("\n")

            f.write("## Daily performance summary (p50/p95)\n\n")
            f.write("See `report.json` for full details (daily metrics + per-day perf schedule).\n")

        return out_dir


if __name__ == "__main__":
    path = seed()
    print(f"Seeded context DB at: {path}")
