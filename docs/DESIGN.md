# Context Layer Design

## Overview

Arcpoint routes inference requests across multiple models and backends. The routing engine needs real-time context to make decisions like:

- Which deployment is healthiest right now?
- Does this user have budget remaining?
- Why did latency spike for premium users?

This system is the **Context Layer** — it ingests signals, stores state, and serves queries to the routing engine and operators.

## Goals

1. **Answer real-time health questions** — Which deployments are up/down/degraded?
2. **Support debugging** — Why did request X go to backend Y? What's causing errors?
3. **Enable trend analysis** — How does today compare to last week?

## Non-Goals (V1)

- Capacity planning / forecasting
- ML model training pipelines
- Real-time quality scoring (use error rate as proxy; async quality scores stored separately)

## Core Concepts

### Deployment = Model + Backend

A model isn't healthy in isolation. GPT-4 on AWS might be healthy while GPT-4 on k8s is degraded. The **deployment** is our unit of health tracking.

Example deployments:
- gpt-4/aws
- gpt-4/k8s
- claude-3/aws
- llama-70b/k8s
- llama-70b/neocloud

### Routing Decision Context

Every request stores *why* it was routed somewhere, not just *where*. This enables debugging. Key queryable fields are denormalized (`model_id`, `backend_id`, `router_version`, `experiment_id`), with full context in a JSON blob for deep debugging.

### Two Query Patterns

| Pattern | Source | Use Case |
|---------|--------|----------|
| Current state | `deployments` table | Routing engine: "what's healthy now?" |
| Historical | `requests` table | Debugging + trends: "what happened?" |

For V1, we compute trends directly from `requests`. Add pre-aggregated rollup tables when query performance becomes an issue.

### Staleness Awareness

Deployment metrics can become stale if health checks fail. The API layer computes staleness from `updated_at` and `sample_count` — we don't trust data that's too old or based on too few samples.

### Budget is Derived

User spend (`daily_budget_used`) is computed from `SUM(requests.cost_usd)`, not stored separately. This ensures consistency and avoids "mystery numbers."

### Incident Scope

Incidents can affect a single deployment, an entire model, or an entire backend. The `target_type` field makes this explicit:
- `deployment` → "llama-70b/neocloud spot preempted"
- `model` → "GPT-4 rate limited globally"
- `backend` → "AWS us-east-1 degraded"

## Entities

| Entity | Description |
|--------|-------------|
| **Deployment** | Model + backend combination with current health/metrics |
| **Request** | Individual inference request with routing decision and cost |
| **User** | Customer with tier, SLA, budget |
| **Incident** | Active or historical incident with flexible scope |
| **Quality Score** | Async evaluation tied to a request (arrives hours later) |

## Key Metrics (per Deployment)

| Metric | Type | Purpose |
|--------|------|---------|
| status | enum | healthy / degraded / down |
| latency_p50_ms | int | Typical latency |
| latency_p95_ms | int | Tail latency |
| error_rate | float | Errors in measurement window |
| error_rate_window_sec | int | Size of measurement window |
| queue_depth | int | Current load |
| cost_per_1k_tokens | float | Current pricing |
| rate_limit_remaining | int | Avoid hitting provider limits |
| sample_count | int | Requests in measurement window |

## Questions the Agent Answers

### Category A: Real-Time Health

- Which deployments are unhealthy right now?
- What's the status of GPT-4 across all backends?
- Are there any active incidents?

### Category B: Debugging

- Why are premium users seeing slow responses?
- Why did request X get routed to backend Y?
- What's causing high error rates on Llama-70b?

### Category C: Trends

- What was p95 latency yesterday vs. last week?
- Which model performs best for summarization tasks?
- How many requests did we serve by tier this week?

## Technology Choices

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Database | SQLite | Simple, portable, sufficient for prototype |
| Agent | Raw Claude function calling | Full control, no abstraction overhead |
| API | Python functions | Direct, testable, can wrap in FastAPI later |
| Timestamps | RFC3339 UTC | Consistent, sortable, SQLite-friendly |

## Extensibility Path

| Future Need | How to Add |
|-------------|------------|
| Model capabilities | Add `models` table with context_window, capabilities JSON |
| Backend regions | Add `backends` table with provider, region |
| Faster trends | Add `deployment_metrics_hourly` rollup table |
| Multiple evaluators | Change quality_scores PK to (request_id, evaluation_type) |
| User preferences | Add columns to users table |

## Open Questions (Deferred)

- How long to retain raw request logs?
- Auto-resolve incidents after N hours, or require manual close?
- Per-user rate limits vs. deployment-level only?
