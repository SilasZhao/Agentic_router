# Database Schema

## Overview

5 tables. SQLite for V1. All timestamps are RFC3339 UTC.

---

## Table: `deployments`

Current state snapshot of each model+backend combination.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Composite key, e.g., "gpt-4/aws" |
| model_id | TEXT | NOT NULL | e.g., "gpt-4" |
| backend_id | TEXT | NOT NULL | e.g., "aws" |
| status | TEXT | NOT NULL | healthy / degraded / down |
| latency_p50_ms | INTEGER | | Current p50 latency |
| latency_p95_ms | INTEGER | | Current p95 latency |
| error_rate | REAL | | 0.0 - 1.0, errors in window |
| error_rate_window_sec | INTEGER | | Measurement window size (e.g., 300) |
| queue_depth | INTEGER | | Current queue length |
| cost_per_1k_tokens | REAL | | Current price in USD |
| rate_limit_remaining | INTEGER | | Remaining calls before provider limit |
| sample_count | INTEGER | | Requests in measurement window |
| updated_at | TEXT | NOT NULL | RFC3339 UTC |

**Constraints:**
```sql
UNIQUE(model_id, backend_id)
```

**Example row:**
```json
{
  "id": "gpt-4/aws",
  "model_id": "gpt-4",
  "backend_id": "aws",
  "status": "healthy",
  "latency_p50_ms": 280,
  "latency_p95_ms": 520,
  "error_rate": 0.02,
  "error_rate_window_sec": 300,
  "queue_depth": 12,
  "cost_per_1k_tokens": 0.03,
  "rate_limit_remaining": 450,
  "sample_count": 127,
  "updated_at": "2024-01-15T14:30:00Z"
}
```

**Notes:**
- Staleness computed by API: if `updated_at` > 60s old or `sample_count` < 10, mark as potentially stale
- `rate_limit_remaining` is deployment-level (provider limits). Per-user limits are a V2 concern.

---

## Table: `requests`

Append-only log of all inference requests with routing context.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | e.g., "req_abc123" |
| user_id | TEXT | NOT NULL | FK to users |
| deployment_id | TEXT | NOT NULL | FK to deployments |
| model_id | TEXT | NOT NULL | Denormalized for fast filtering |
| backend_id | TEXT | NOT NULL | Denormalized for fast filtering |
| task_type | TEXT | | summarization / coding / chat / etc. |
| input_tokens | INTEGER | | Request size |
| output_tokens | INTEGER | | Response size |
| cost_usd | REAL | | Actual cost at decision time |
| latency_ms | INTEGER | | Actual end-to-end latency |
| status | TEXT | NOT NULL | success / error / timeout |
| router_version | TEXT | | e.g., "v1.2.3" |
| experiment_id | TEXT | | Nullable, for A/B tests |
| routing_reason | TEXT | | JSON blob for deep debugging |
| created_at | TEXT | NOT NULL | RFC3339 UTC |

**Example row:**
```json
{
  "id": "req_abc123",
  "user_id": "user_456",
  "deployment_id": "gpt-4/aws",
  "model_id": "gpt-4",
  "backend_id": "aws",
  "task_type": "summarization",
  "input_tokens": 1500,
  "output_tokens": 350,
  "cost_usd": 0.055,
  "latency_ms": 342,
  "status": "success",
  "router_version": "v1.2.0",
  "experiment_id": null,
  "routing_reason": "{\"user_tier\":\"premium\",\"options_considered\":[...],\"decision\":\"best latency\"}",
  "created_at": "2024-01-15T14:32:15Z"
}
```

**routing_reason JSON structure:**
```json
{
  "user_tier": "premium",
  "latency_sla_ms": 500,
  "options_considered": [
    {"deployment": "gpt-4/aws", "estimated_latency_ms": 320, "available": true},
    {"deployment": "gpt-4/k8s", "estimated_latency_ms": null, "available": false, "reason": "degraded"}
  ],
  "decision": "gpt-4/aws: only healthy option meeting SLA"
}
```

**Notes:**
- `model_id` and `backend_id` are denormalized from `deployment_id` for query performance
- `cost_usd` is computed at request time using snapshot of `deployments.cost_per_1k_tokens`
- Keep `routing_reason` JSON flexible; queryable fields are denormalized

---

## Table: `users`

Customer configuration. Budget usage is derived from requests.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | e.g., "user_456" |
| tier | TEXT | NOT NULL | premium / standard / budget |
| latency_sla_ms | INTEGER | | Max acceptable latency |
| daily_budget_usd | REAL | | Spending limit per day |

**Example row:**
```json
{
  "id": "user_456",
  "tier": "premium",
  "latency_sla_ms": 500,
  "daily_budget_usd": 50.00
}
```

**Derived fields (computed by API, not stored):**
```sql
-- Daily budget used
SELECT COALESCE(SUM(cost_usd), 0) as daily_budget_used
FROM requests
WHERE user_id = ? AND DATE(created_at) = DATE('now');

-- Budget remaining
daily_budget_usd - daily_budget_used
```

---

## Table: `incidents`

Active and historical incidents with flexible scope.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | e.g., "inc_789" |
| target_type | TEXT | NOT NULL | deployment / model / backend |
| target_id | TEXT | NOT NULL | What's affected |
| title | TEXT | NOT NULL | Short description |
| status | TEXT | NOT NULL | active / resolved |
| started_at | TEXT | NOT NULL | RFC3339 UTC |
| resolved_at | TEXT | | Null if active |

**Example rows:**
```json
// Deployment-level incident
{
  "id": "inc_001",
  "target_type": "deployment",
  "target_id": "llama-70b/neocloud",
  "title": "Spot instance preempted",
  "status": "active",
  "started_at": "2024-01-15T12:00:00Z",
  "resolved_at": null
}

// Backend-level incident
{
  "id": "inc_002",
  "target_type": "backend",
  "target_id": "aws",
  "title": "AWS us-east-1 elevated latency",
  "status": "resolved",
  "started_at": "2024-01-14T08:00:00Z",
  "resolved_at": "2024-01-14T11:30:00Z"
}

// Model-level incident
{
  "id": "inc_003",
  "target_type": "model",
  "target_id": "gpt-4",
  "title": "GPT-4 rate limits reduced globally",
  "status": "resolved",
  "started_at": "2024-01-10T00:00:00Z",
  "resolved_at": "2024-01-10T06:00:00Z"
}
```

---

## Table: `quality_scores`

Async evaluations that arrive hours after requests.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| request_id | TEXT | PRIMARY KEY | FK to requests, 1:1 for V1 |
| score | REAL | NOT NULL | 0.0 - 1.0 |
| evaluated_at | TEXT | NOT NULL | RFC3339 UTC |

**Example row:**
```json
{
  "request_id": "req_abc123",
  "score": 0.87,
  "evaluated_at": "2024-01-15T18:45:00Z"
}
```

**Notes:**
- V1 assumes 1:1 relationship (one score per request)
- ~60% of requests get scores (async, some never evaluated)
- To support multiple evaluators later, change PK to `(request_id, evaluation_type)`

---

## Indexes

```sql
-- Fast filtering by model or backend
CREATE INDEX idx_deployments_model ON deployments(model_id);
CREATE INDEX idx_deployments_backend ON deployments(backend_id);

-- Request queries by user, time, deployment
CREATE INDEX idx_requests_user ON requests(user_id);
CREATE INDEX idx_requests_created ON requests(created_at);
CREATE INDEX idx_requests_deployment ON requests(deployment_id);
CREATE INDEX idx_requests_model ON requests(model_id);

-- Active incidents
CREATE INDEX idx_incidents_status ON incidents(status);
CREATE INDEX idx_incidents_target ON incidents(target_type, target_id);
```

---

## SQL Schema

```sql
CREATE TABLE deployments (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    backend_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('healthy', 'degraded', 'down')),
    latency_p50_ms INTEGER,
    latency_p95_ms INTEGER,
    error_rate REAL CHECK (error_rate >= 0 AND error_rate <= 1),
    error_rate_window_sec INTEGER,
    queue_depth INTEGER,
    cost_per_1k_tokens REAL,
    rate_limit_remaining INTEGER,
    sample_count INTEGER,
    updated_at TEXT NOT NULL,
    UNIQUE(model_id, backend_id)
);

CREATE TABLE users (
    id TEXT PRIMARY KEY,
    tier TEXT NOT NULL CHECK (tier IN ('premium', 'standard', 'budget')),
    latency_sla_ms INTEGER,
    daily_budget_usd REAL
);

CREATE TABLE requests (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    deployment_id TEXT NOT NULL REFERENCES deployments(id),
    model_id TEXT NOT NULL,
    backend_id TEXT NOT NULL,
    task_type TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    status TEXT NOT NULL CHECK (status IN ('success', 'error', 'timeout')),
    router_version TEXT,
    experiment_id TEXT,
    routing_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE incidents (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('deployment', 'model', 'backend')),
    target_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'resolved')),
    started_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE quality_scores (
    request_id TEXT PRIMARY KEY REFERENCES requests(id),
    score REAL NOT NULL CHECK (score >= 0 AND score <= 1),
    evaluated_at TEXT NOT NULL
);

-- Indexes
CREATE INDEX idx_deployments_model ON deployments(model_id);
CREATE INDEX idx_deployments_backend ON deployments(backend_id);
CREATE INDEX idx_requests_user ON requests(user_id);
CREATE INDEX idx_requests_created ON requests(created_at);
CREATE INDEX idx_requests_deployment ON requests(deployment_id);
CREATE INDEX idx_requests_model ON requests(model_id);
CREATE INDEX idx_incidents_status ON incidents(status);
CREATE INDEX idx_incidents_target ON incidents(target_type, target_id);
```
