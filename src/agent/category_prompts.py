from __future__ import annotations

"""Category-specific planning prompts and tool subsets.

These are fed into the planner (qwen3:8b) to produce JSON plans.
"""

from dataclasses import dataclass

from src.agent.categories import QueryCategory


@dataclass(frozen=True)
class CategoryPlanConfig:
    tool_subset: list[str]
    system_prompt: str


CLASSIFIER_FEWSHOT: list[tuple[str, QueryCategory, bool]] = [
    # STATUS examples (from STATUS system_prompt)
    ("Are there any active incidents?", QueryCategory.STATUS, False),
    ("Which deployments are unhealthy right now?", QueryCategory.STATUS, False),
    ("How many deployments do we have?", QueryCategory.STATUS, False),
    ("Count deployments by backend_id", QueryCategory.STATUS, True),
    # LOOKUP examples (from LOOKUP system_prompt)
    ("Explain why request req_abc123 was routed to AWS", QueryCategory.LOOKUP, False),
    ("What is user_456's budget status today?", QueryCategory.LOOKUP, False),
    # INVESTIGATE examples (from INVESTIGATE system_prompt)
    ("Why are premium users seeing slow responses?", QueryCategory.INVESTIGATE, True),
    ("What's causing errors on llama-70b?", QueryCategory.INVESTIGATE, True),
    # TRENDS examples (from TRENDS system_prompt)
    ("How many requests did we serve by tier this week?", QueryCategory.TRENDS, False),
    ("p95 latency yesterday vs last week?", QueryCategory.TRENDS, True),
    # NOVEL (generic)
    ("Some new question that doesn't match known patterns", QueryCategory.NOVEL, True),
]


def classifier_fewshot_block() -> str:
    lines: list[str] = []
    for q, cat, is_complex in CLASSIFIER_FEWSHOT:
        lines.append(f"Q: {q}")
        lines.append(f"A: {{\"category\":\"{cat.value}\",\"is_complex\":{str(is_complex).lower()}}}")
    return "\n".join(lines) + "\n"


SCHEMA_INTRO = """Database schema (SQLite, V2) — key tables/columns for safe_sql_query:

Dimensions:
- deployments(id, model_id, backend_id, enabled, weight, created_at)
- models(id, provider, max_context_tokens, supports_streaming, supports_tools, supports_json_mode, notes)
- backends(id, provider, region, backend_type, notes)
- users(id, tier_id, daily_budget_usd, latency_sla_p95_ms_override, preferences_json)
- tiers(id, latency_sla_p95_ms, sla_window_sec, max_error_rate, max_timeout_rate)

Current snapshot:
- deployment_state_current(deployment_id, status, window_sec, sample_count, updated_at, latency_p50_ms, latency_p95_ms, error_rate, timeout_rate, queue_depth, rate_limit_remaining, ttft_p50_ms, ttft_p95_ms, decode_toks_per_sec_p50, decode_toks_per_sec_p95)

Event history:
- requests(id, created_at, user_id, deployment_id, model_id, backend_id, task_type, input_tokens, output_tokens, latency_ms, ttft_ms, decode_toks_per_sec, cost_usd, status, error_code, router_version, experiment_id, routing_reason_json)
- quality_scores(request_id, eval_type, score, evaluated_at)
- incidents(id, target_type, target_id, title, status, started_at, resolved_at)

Notes for safe_sql_query:
- Prefer querying deployments + deployment_state_current for current health questions.
- Prefer querying requests for historical traffic/latency/errors.
"""


TOOL_GLOSSARY = """Tool glossary (name -> purpose -> key args):
- get_active_incidents: list currently active incidents.
  - args: target_type (deployment|model|backend, optional), target_id (optional)
  - use when: any \"what's happening now\" or root-cause investigation

- get_deployment_status: current health snapshot of deployments.
  - args: model_id (optional), backend_id (optional), status (healthy|degraded|down, optional)
  - use when: health/staleness/unhealthy questions; always useful for ops checks

- get_recent_requests: recent request samples (for debugging).
  - args: since/until (\"2 hours ago\", \"yesterday\", RFC3339Z), limit (<=500), filters: user_id, user_tier, deployment_id, model_id, backend_id, status
  - use when: you need evidence examples (who is slow, where errors happen)

- get_request_detail: full detail for one request.
  - args: request_id (required)
  - use when: question mentions req_...

- get_user_context: tier + budget usage for one user.
  - args: user_id (required)
  - use when: question mentions user_... or asks about budget/SLA

- get_latency_trends: p50/p95 and error_rate over time.
  - args: since/until, granularity (hour|day), optional filters: deployment_id/model_id/backend_id
  - use when: \"spike\", \"yesterday vs last week\", tail latency questions

- get_quality_summary: aggregated quality scores.
  - args: since/until, optional model_id, task_type
  - use when: \"quality drop\", \"best model for summarization\"

- get_request_volume: traffic volume over time (counts + cost).
  - args: group_by (tier|model|backend|deployment), since/until, granularity (hour|day)
  - use when: traffic/cost breakdown questions

- safe_sql_query: guarded ad-hoc SELECT for edge cases not covered by domain tools.
  - args: query (SELECT/WITH only), params (optional), max_rows (default 100), timeout_sec (default 5)
  - use when: you need a custom join/aggregation not exposed by domain tools.
  - avoid when: a domain tool already answers it.
"""


CATEGORY_PLAN_CONFIG: dict[QueryCategory, CategoryPlanConfig] = {
    QueryCategory.STATUS: CategoryPlanConfig(
        tool_subset=["get_active_incidents", "get_deployment_status", "safe_sql_query"],
        system_prompt=(
            "You are handling a STATUS query: health + incidents + simple counts.\n"
            "Use domain tools first. Only use safe_sql_query if the user asks for a count/breakdown not directly provided.\n\n"
            f"{SCHEMA_INTRO}\n\n"
            f"{TOOL_GLOSSARY}\n\n"
            "Examples (each step includes why + args):\n"
            "Q: Are there any active incidents?\n"
            "A(plan): {\"query_intent\":\"ops_investigation\",\"steps\":["
            "{\"tool_name\":\"get_active_incidents\",\"args\":{},\"purpose\":\"Check if the system has any active incidents to explain anomalies\"}"
            "],\"uncertainties\":[],\"verification_tips\":[]}\n\n"
            "Q: Which deployments are unhealthy right now?\n"
            "A(plan): {\"query_intent\":\"ops_investigation\",\"steps\":["
            "{\"tool_name\":\"get_active_incidents\",\"args\":{},\"purpose\":\"Incidents first: they can explain degraded/down\"},"
            "{\"tool_name\":\"get_deployment_status\",\"args\":{},\"purpose\":\"List deployments and filter status=degraded/down in the answer\"}"
            "],\"uncertainties\":[],\"verification_tips\":[]}\n\n"
            "Q: How many deployments do we have?\n"
            "A(plan): {\"query_intent\":\"other\",\"steps\":["
            "{\"tool_name\":\"get_deployment_status\",\"args\":{},\"purpose\":\"Uses summary.total for count (fast, domain tool)\"}"
            "],\"uncertainties\":[],\"verification_tips\":[]}\n\n"
            "Q: Count deployments by backend_id (not directly provided).\n"
            "A(plan): {\"query_intent\":\"other\",\"steps\":["
            "{\"tool_name\":\"safe_sql_query\",\"args\":{"
            "\"query\":\"SELECT backend_id, COUNT(*) AS deployments FROM deployments GROUP BY backend_id ORDER BY deployments DESC\""
            "},\"purpose\":\"Need a custom aggregation; domain tools don’t expose grouping by backend\"}"
            "],\"uncertainties\":[],\"verification_tips\":[]}\n"
        ),
    ),
    QueryCategory.LOOKUP: CategoryPlanConfig(
        tool_subset=["get_request_detail", "get_user_context", "get_recent_requests"],
        system_prompt=(
            "You are handling a LOOKUP query: specific request/user drill-down.\n"
            "If the query contains req_..., call get_request_detail(request_id=...).\n"
            "If the query contains user_..., call get_user_context(user_id=...) and optionally get_recent_requests(user_id=..., limit=20).\n\n"
            f"{SCHEMA_INTRO}\n\n"
            f"{TOOL_GLOSSARY}\n\n"
            "Examples:\n"
            "Q: Explain why request req_abc123 was routed to AWS\n"
            "A(plan): {\"query_intent\":\"request_lookup\",\"steps\":["
            "{\"tool_name\":\"get_request_detail\",\"args\":{\"request_id\":\"req_abc123\"},\"purpose\":\"Fetch routing_decision and related incident/quality for this specific request\"}"
            "],\"uncertainties\":[],\"verification_tips\":[]}\n\n"
            "Q: What is user_456's budget status today?\n"
            "A(plan): {\"query_intent\":\"user_lookup\",\"steps\":["
            "{\"tool_name\":\"get_user_context\",\"args\":{\"user_id\":\"user_456\"},\"purpose\":\"Compute daily budget used/remaining from requests\"}"
            "],\"uncertainties\":[],\"verification_tips\":[]}\n"
        ),
    ),
    QueryCategory.INVESTIGATE: CategoryPlanConfig(
        tool_subset=[
            "get_active_incidents",
            "get_deployment_status",
            "get_recent_requests",
            "get_latency_trends",
            "get_quality_summary",
            "get_request_detail",
            "safe_sql_query",
        ],
        system_prompt=(
            "You are handling an INVESTIGATE query: debugging / root-cause / why.\n"
            "General playbook:\n"
            "- Start with get_active_incidents + get_deployment_status unless the user explicitly asks for a purely historical look.\n"
            "- Then choose ONE of: recent samples (get_recent_requests), trends (get_latency_trends/get_quality_summary), or drill-down (get_request_detail).\n"
            "- Use safe_sql_query only if you need a custom join/aggregation not provided.\n\n"
            f"{SCHEMA_INTRO}\n\n"
            f"{TOOL_GLOSSARY}\n\n"
            "Examples:\n"
            "Q: Why are premium users seeing slow responses?\n"
            "A(plan): {\"query_intent\":\"ops_investigation\",\"steps\":["
            "{\"tool_name\":\"get_active_incidents\",\"args\":{},\"purpose\":\"Incidents can explain latency spikes\"},"
            "{\"tool_name\":\"get_deployment_status\",\"args\":{},\"purpose\":\"Check if any deployments are degraded/down or stale\"},"
            "{\"tool_name\":\"get_recent_requests\",\"args\":{\"user_tier\":\"premium\",\"since\":\"2 hours ago\",\"limit\":50},\"purpose\":\"Get evidence: recent premium latencies and where they routed\"}"
            "],\"uncertainties\":[\"Time window assumed (last 2 hours)\"],\"verification_tips\":[\"If needed, rerun with since='yesterday' or filter by backend_id/model_id\"]}\n\n"
            "Q: What's causing errors on llama-70b?\n"
            "A(plan): {\"query_intent\":\"ops_investigation\",\"steps\":["
            "{\"tool_name\":\"get_active_incidents\",\"args\":{},\"purpose\":\"See if there is an active incident affecting llama-70b or its backends\"},"
            "{\"tool_name\":\"get_deployment_status\",\"args\":{\"model_id\":\"llama-70b\"},\"purpose\":\"Check health snapshot for llama-70b deployments\"},"
            "{\"tool_name\":\"get_recent_requests\",\"args\":{\"model_id\":\"llama-70b\",\"status\":\"error\",\"since\":\"2 hours ago\",\"limit\":50},\"purpose\":\"Sample error requests for patterns (backend, error clustering)\"}"
            "],\"uncertainties\":[],\"verification_tips\":[]}\n"
        ),
    ),
    QueryCategory.TRENDS: CategoryPlanConfig(
        tool_subset=["get_request_volume", "get_latency_trends", "get_quality_summary"],
        system_prompt=(
            "You are handling a TRENDS query: time-based aggregates and comparisons.\n"
            "Choose the right tool:\n"
            "- request counts/cost: get_request_volume\n"
            "- latency metrics: get_latency_trends\n"
            "- quality metrics: get_quality_summary\n"
            "Default windows:\n"
            "- if user says \"this week\" use since=\"7 days ago\".\n"
            "- prefer granularity=day for comparisons, granularity=hour for short windows.\n\n"
            f"{SCHEMA_INTRO}\n\n"
            f"{TOOL_GLOSSARY}\n\n"
            "Examples:\n"
            "Q: How many requests did we serve by tier this week?\n"
            "A(plan): {\"query_intent\":\"traffic\",\"steps\":["
            "{\"tool_name\":\"get_request_volume\",\"args\":{\"group_by\":\"tier\",\"since\":\"7 days ago\",\"until\":\"now\",\"granularity\":\"day\"},\"purpose\":\"Daily request counts and cost by tier\"}"
            "],\"uncertainties\":[],\"verification_tips\":[]}\n\n"
            "Q: p95 latency yesterday vs last week?\n"
            "A(plan): {\"query_intent\":\"traffic\",\"steps\":["
            "{\"tool_name\":\"get_latency_trends\",\"args\":{\"since\":\"7 days ago\",\"until\":\"now\",\"granularity\":\"day\"},\"purpose\":\"Get daily p95 so you can compare yesterday to prior days\"}"
            "],\"uncertainties\":[\"Interpretation of 'last week' can vary; using 7 days\"],\"verification_tips\":[]}\n"
        ),
    ),
}
