# Agent Tools

## Overview

These are the tools available to the LLM agent for querying the Context Layer. Each tool maps to a Python function in `src/context/api.py`.

Design principles:
- Tools return structured data (dicts/lists), agent formats for humans
- Parameters are minimal and composable
- Time parameters accept ISO strings or relative strings ("1 hour ago", "yesterday")
- Empty results return empty list, not None

---

## Category A: Real-Time Health

### `get_deployment_status`

Get current health status of deployments.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| model_id | string | no | Filter by model (e.g., "gpt-4") |
| backend_id | string | no | Filter by backend (e.g., "aws") |
| status | string | no | Filter by status: healthy / degraded / down |

**Returns:**
```json
{
  "deployments": [
    {
      "id": "gpt-4/aws",
      "model_id": "gpt-4",
      "backend_id": "aws",
      "status": "healthy",
      "latency_p50_ms": 280,
      "latency_p95_ms": 520,
      "error_rate": 0.02,
      "queue_depth": 12,
      "cost_per_1k_tokens": 0.03,
      "rate_limit_remaining": 450,
      "sample_count": 127,
      "updated_at": "2024-01-15T14:30:00Z",
      "is_stale": false
    }
  ],
  "summary": {
    "total": 6,
    "healthy": 4,
    "degraded": 1,
    "down": 1
  }
}
```

**Example usage:**
- "Which deployments are unhealthy?" → `get_deployment_status(status="degraded")` + `get_deployment_status(status="down")`
- "Status of GPT-4?" → `get_deployment_status(model_id="gpt-4")`

---

### `get_active_incidents`

Get currently active incidents.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| target_type | string | no | Filter: deployment / model / backend |
| target_id | string | no | Filter by specific target |

**Returns:**
```json
{
  "incidents": [
    {
      "id": "inc_001",
      "target_type": "deployment",
      "target_id": "llama-70b/neocloud",
      "title": "Spot instance preempted",
      "started_at": "2024-01-15T12:00:00Z",
      "duration_minutes": 150
    }
  ],
  "count": 1
}
```

**Example usage:**
- "Any active incidents?" → `get_active_incidents()`
- "Incidents affecting AWS?" → `get_active_incidents(target_type="backend", target_id="aws")`

---

## Category B: Debugging

### `get_recent_requests`

Get recent requests with optional filters.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| user_id | string | no | Filter by user |
| user_tier | string | no | Filter by tier: premium / standard / budget |
| deployment_id | string | no | Filter by deployment |
| model_id | string | no | Filter by model |
| backend_id | string | no | Filter by backend |
| status | string | no | Filter: success / error / timeout |
| since | string | no | Start time (ISO or relative like "1 hour ago") |
| until | string | no | End time (ISO or relative) |
| limit | integer | no | Max results, default 50 |

**Returns:**
```json
{
  "requests": [
    {
      "id": "req_abc123",
      "user_id": "user_456",
      "user_tier": "premium",
      "deployment_id": "gpt-4/aws",
      "model_id": "gpt-4",
      "backend_id": "aws",
      "task_type": "summarization",
      "latency_ms": 342,
      "cost_usd": 0.055,
      "status": "success",
      "created_at": "2024-01-15T14:32:15Z"
    }
  ],
  "count": 1,
  "has_more": false
}
```

**Example usage:**
- "Why are premium users slow?" → `get_recent_requests(user_tier="premium", since="1 hour ago")`
- "Recent errors on Llama?" → `get_recent_requests(model_id="llama-70b", status="error")`

---

### `get_request_detail`

Get full details of a single request including routing decision.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| request_id | string | yes | Request ID |

**Returns:**
```json
{
  "request": {
    "id": "req_abc123",
    "user_id": "user_456",
    "user_tier": "premium",
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
    "created_at": "2024-01-15T14:32:15Z",
    "routing_decision": {
      "user_tier": "premium",
      "latency_sla_ms": 500,
      "options_considered": [
        {"deployment": "gpt-4/aws", "estimated_latency_ms": 320, "available": true},
        {"deployment": "gpt-4/k8s", "estimated_latency_ms": null, "available": false, "reason": "degraded"}
      ],
      "decision": "gpt-4/aws: only healthy option meeting SLA"
    }
  },
  "quality_score": 0.87,
  "related_incident": null
}
```

**Example usage:**
- "Why did request X go to AWS?" → `get_request_detail(request_id="req_abc123")`

---

### `get_user_context`

Get user info including current budget usage.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| user_id | string | yes | User ID |

**Returns:**
```json
{
  "user": {
    "id": "user_456",
    "tier": "premium",
    "latency_sla_ms": 500,
    "daily_budget_usd": 50.00,
    "daily_budget_used_usd": 12.35,
    "daily_budget_remaining_usd": 37.65,
    "requests_today": 45
  }
}
```

---

## Category C: Trends

### `get_latency_trends`

Get latency metrics over time.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| deployment_id | string | no | Filter by deployment |
| model_id | string | no | Filter by model |
| backend_id | string | no | Filter by backend |
| since | string | no | Start time, default "24 hours ago" |
| until | string | no | End time, default "now" |
| granularity | string | no | hour / day, default "hour" |

**Returns:**
```json
{
  "data": [
    {
      "period": "2024-01-15T14:00:00Z",
      "deployment_id": "gpt-4/aws",
      "request_count": 45,
      "latency_p50_ms": 275,
      "latency_p95_ms": 510,
      "error_rate": 0.02
    }
  ],
  "summary": {
    "total_requests": 523,
    "avg_latency_p50_ms": 285,
    "avg_latency_p95_ms": 498
  }
}
```

**Example usage:**
- "p95 latency yesterday vs today?" → `get_latency_trends(since="yesterday", until="now")`
- "Latency trend for GPT-4 last week?" → `get_latency_trends(model_id="gpt-4", since="7 days ago")`

---

### `get_quality_summary`

Get quality scores aggregated by model and task type.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| model_id | string | no | Filter by model |
| task_type | string | no | Filter by task type |
| since | string | no | Start time, default "7 days ago" |
| until | string | no | End time, default "now" |

**Returns:**
```json
{
  "data": [
    {
      "model_id": "gpt-4",
      "task_type": "summarization",
      "avg_score": 0.91,
      "min_score": 0.72,
      "max_score": 0.98,
      "sample_count": 128
    },
    {
      "model_id": "gpt-4",
      "task_type": "coding",
      "avg_score": 0.88,
      "min_score": 0.65,
      "max_score": 0.97,
      "sample_count": 95
    }
  ]
}
```

**Example usage:**
- "Best model for summarization?" → `get_quality_summary(task_type="summarization")`
- "Quality by task type for GPT-4?" → `get_quality_summary(model_id="gpt-4")`

---

### `get_request_volume`

Get request counts over time, grouped by various dimensions.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| group_by | string | no | tier / model / backend / deployment, default "tier" |
| since | string | no | Start time, default "7 days ago" |
| until | string | no | End time, default "now" |
| granularity | string | no | hour / day, default "day" |

**Returns:**
```json
{
  "data": [
    {
      "period": "2024-01-15",
      "group": "premium",
      "request_count": 245,
      "total_cost_usd": 18.45
    },
    {
      "period": "2024-01-15",
      "group": "standard",
      "request_count": 412,
      "total_cost_usd": 12.30
    }
  ],
  "totals": {
    "premium": {"requests": 1205, "cost_usd": 89.20},
    "standard": {"requests": 2104, "cost_usd": 63.50},
    "budget": {"requests": 891, "cost_usd": 8.90}
  }
}
```

**Example usage:**
- "Requests by tier this week?" → `get_request_volume(group_by="tier", since="7 days ago")`
- "Traffic by model today?" → `get_request_volume(group_by="model", since="today")`

---

## Tool Definition Format (for Claude)

```python
TOOLS = [
    {
        "name": "get_deployment_status",
        "description": "Get current health status of deployments. Can filter by model, backend, or status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model_id": {"type": "string", "description": "Filter by model ID"},
                "backend_id": {"type": "string", "description": "Filter by backend ID"},
                "status": {"type": "string", "enum": ["healthy", "degraded", "down"]}
            },
            "required": []
        }
    },
    # ... etc
]
```

---

## Error Handling

All tools return errors in consistent format:

```json
{
  "error": true,
  "message": "Request not found: req_xyz",
  "code": "NOT_FOUND"
}
```

Error codes: `NOT_FOUND`, `INVALID_PARAMETER`, `DATABASE_ERROR`
