-- SQLite schema for the Context Layer (V2)
--
-- Design principles:
-- - Dimensions (models/backends/deployments) are stable and normalized.
-- - "Current state" metrics are a snapshot (deployment_state_current).
-- - Events (requests/quality_scores/incidents) are historical and append-only.
-- - SLA is represented at the tier level (simple V1), with optional per-user overrides.

-- -----------------------------
-- Dimensions (stable metadata)
-- -----------------------------

CREATE TABLE models (
    id TEXT PRIMARY KEY,
    provider TEXT,
    max_context_tokens INTEGER,
    supports_streaming INTEGER NOT NULL DEFAULT 1 CHECK (supports_streaming IN (0, 1)),
    supports_tools INTEGER NOT NULL DEFAULT 0 CHECK (supports_tools IN (0, 1)),
    supports_json_mode INTEGER NOT NULL DEFAULT 0 CHECK (supports_json_mode IN (0, 1)),
    notes TEXT
);

CREATE TABLE backends (
    id TEXT PRIMARY KEY,
    provider TEXT,
    region TEXT,
    backend_type TEXT,
    notes TEXT
);

-- Deployment = model + backend (unit of routing).
CREATE TABLE deployments (
    id TEXT PRIMARY KEY, -- e.g. "gpt-4/aws"
    model_id TEXT NOT NULL REFERENCES models(id),
    backend_id TEXT NOT NULL REFERENCES backends(id),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    weight REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    UNIQUE(model_id, backend_id)
);

-- ------------------------------------
-- SLA + user context (personalization)
-- ------------------------------------

-- Simple SLA: per tier, define tail latency target and reliability thresholds
-- over a specific rolling window. This makes "SLA" concrete and queryable.
CREATE TABLE tiers (
    id TEXT PRIMARY KEY CHECK (id IN ('premium', 'standard', 'budget')),
    latency_sla_p95_ms INTEGER NOT NULL,
    sla_window_sec INTEGER NOT NULL, -- the window over which p95 is computed (e.g. 300s)
    max_error_rate REAL NOT NULL CHECK (max_error_rate >= 0 AND max_error_rate <= 1),
    max_timeout_rate REAL NOT NULL CHECK (max_timeout_rate >= 0 AND max_timeout_rate <= 1)
);

CREATE TABLE users (
    id TEXT PRIMARY KEY,
    tier_id TEXT NOT NULL REFERENCES tiers(id),
    daily_budget_usd REAL,
    -- Optional per-user overrides (NULL => inherit tier defaults)
    latency_sla_p95_ms_override INTEGER,
    max_error_rate_override REAL CHECK (max_error_rate_override IS NULL OR (max_error_rate_override >= 0 AND max_error_rate_override <= 1)),
    max_timeout_rate_override REAL CHECK (max_timeout_rate_override IS NULL OR (max_timeout_rate_override >= 0 AND max_timeout_rate_override <= 1)),
    preferences_json TEXT
);

-- -------------------------------------
-- Current state (near-real-time snapshot)
-- -------------------------------------

-- Latest deployment health + performance snapshot. Router hot-path would typically
-- read from a cache, but this remains the source-of-truth snapshot for analytics
-- and operator visibility.
CREATE TABLE deployment_state_current (
    deployment_id TEXT PRIMARY KEY REFERENCES deployments(id),
    status TEXT NOT NULL CHECK (status IN ('healthy', 'degraded', 'down')),
    window_sec INTEGER NOT NULL,
    sample_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL,

    latency_p50_ms INTEGER,
    latency_p95_ms INTEGER,
    error_rate REAL CHECK (error_rate IS NULL OR (error_rate >= 0 AND error_rate <= 1)),
    timeout_rate REAL CHECK (timeout_rate IS NULL OR (timeout_rate >= 0 AND timeout_rate <= 1)),
    queue_depth INTEGER,
    rate_limit_remaining INTEGER,

    -- Token-length-aware components
    ttft_p50_ms INTEGER,
    ttft_p95_ms INTEGER,
    decode_toks_per_sec_p50 REAL,
    decode_toks_per_sec_p95 REAL
);

-- -------------
-- Event history
-- -------------

CREATE TABLE requests (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    deployment_id TEXT NOT NULL REFERENCES deployments(id),

    -- Denormalized dims for fast filtering (kept consistent with deployment_id at write time)
    model_id TEXT NOT NULL,
    backend_id TEXT NOT NULL,

    task_type TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,

    -- Total and component latencies
    latency_ms INTEGER,
    ttft_ms INTEGER,
    decode_toks_per_sec REAL,

    cost_usd REAL,
    status TEXT NOT NULL CHECK (status IN ('success', 'error', 'timeout')),
    error_code TEXT,

    router_version TEXT,
    experiment_id TEXT,
    routing_reason_json TEXT
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

-- Allow multiple evaluators/signals per request (async feedback).
CREATE TABLE quality_scores (
    request_id TEXT NOT NULL REFERENCES requests(id),
    eval_type TEXT NOT NULL DEFAULT 'offline',
    score REAL NOT NULL CHECK (score >= 0 AND score <= 1),
    evaluated_at TEXT NOT NULL,
    PRIMARY KEY (request_id, eval_type)
);

-- -------
-- Indexes
-- -------

CREATE INDEX idx_deployments_model ON deployments(model_id);
CREATE INDEX idx_deployments_backend ON deployments(backend_id);

CREATE INDEX idx_requests_user ON requests(user_id);
CREATE INDEX idx_requests_created ON requests(created_at);
CREATE INDEX idx_requests_deployment ON requests(deployment_id);
CREATE INDEX idx_requests_model ON requests(model_id);
CREATE INDEX idx_requests_backend ON requests(backend_id);

CREATE INDEX idx_incidents_status ON incidents(status);
CREATE INDEX idx_incidents_target ON incidents(target_type, target_id);

