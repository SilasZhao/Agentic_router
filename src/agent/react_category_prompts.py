from __future__ import annotations

"""ReAct-oriented category prompts.

These prompts are meant for *tool-calling ReAct loops*, not for JSON-plan generation.

They include:
- DB schema intro (for safe_sql_query)
- Tool glossary (what tools mean + key args)
- ReAct-friendly guidance + examples per category
"""

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
  - use when: any "what's happening now" or root-cause investigation

- get_deployment_status: current health snapshot of deployments.
  - args: model_id (optional), backend_id (optional), status (healthy|degraded|down, optional)
  - use when: health/staleness/unhealthy questions; always useful for ops checks

- get_recent_requests: recent request samples (for debugging).
  - args: since/until ("2 hours ago", "yesterday", RFC3339Z), limit (<=500), filters: user_id, user_tier, deployment_id, model_id, backend_id, status
  - use when: you need evidence examples (who is slow, where errors happen)

- get_request_detail: full detail for one request.
  - args: request_id (required)
  - use when: question mentions req_...

- get_user_context: tier + budget usage for one user.
  - args: user_id (required)
  - use when: question mentions user_... or asks about budget/SLA

- get_latency_trends: p50/p95 and error_rate over time.
  - args: since/until, granularity (hour|day), optional filters: deployment_id/model_id/backend_id
  - use when: "spike", "yesterday vs last week", tail latency questions

- get_quality_summary: aggregated quality scores.
  - args: since/until, optional model_id, task_type
  - use when: "quality drop", "best model for summarization"

- get_request_volume: traffic volume over time (counts + cost).
  - args: group_by (tier|model|backend|deployment), since/until, granularity (hour|day)
  - use when: traffic/cost breakdown questions

- safe_sql_query: guarded ad-hoc SELECT for edge cases not covered by domain tools.
  - args: query (SELECT/WITH only), params (optional), max_rows (default 100), timeout_sec (default 5)
  - use when: you need a custom join/aggregation not exposed by domain tools.
  - avoid when: a domain tool already answers it.
"""


Category = str  # "STATUS" | "LOOKUP" | "INVESTIGATE" | "TRENDS" | "NOVEL"


def _react_examples(category: Category) -> str:
    # Keep examples short and explicitly tool-calling oriented.
    if category == "STATUS":
        return (
            "Examples (ReAct style):\n"
            "User: Which deployments are unhealthy right now?\n"
            "Assistant: (call get_active_incidents {})\n"
            "Assistant: (call get_deployment_status {})\n"
            "Assistant: Answer: list degraded/down deployments; mention any active incidents.\n\n"
            "User: Count deployments by backend_id\n"
            "Assistant: (call safe_sql_query {query: \"SELECT backend_id, COUNT(*) AS deployments FROM deployments GROUP BY backend_id ORDER BY deployments DESC\"})\n"
            "Assistant: Answer: return grouped counts.\n"
        )

    if category == "LOOKUP":
        return (
            "Examples (ReAct style):\n"
            "User: Explain why request req_abc123 was routed to AWS\n"
            "Assistant: (call get_request_detail {request_id: \"req_abc123\"})\n"
            "Assistant: Answer: explain routing_reason_json / backend_id / any errors.\n\n"
            "User: What is user_456's budget status today?\n"
            "Assistant: (call get_user_context {user_id: \"user_456\"})\n"
            "Assistant: Answer: summarize tier, budget used/remaining.\n"
        )

    if category == "INVESTIGATE":
        return (
            "Examples (ReAct style):\n"
            "User: Why are premium users seeing slow responses?\n"
            "Assistant: (call get_active_incidents {})\n"
            "Assistant: (call get_deployment_status {})\n"
            "Assistant: (call get_recent_requests {user_tier:\"premium\", since:\"2 hours ago\", limit:50})\n"
            "Assistant: Answer: cite evidence (latency_ms/ttft_ms), identify affected deployments/backends, suggest next filter if needed.\n"
        )

    if category == "TRENDS":
        return (
            "Examples (ReAct style):\n"
            "User: How many requests did we serve by tier this week?\n"
            "Assistant: (call get_request_volume {group_by:\"tier\", since:\"7 days ago\", until:\"now\", granularity:\"day\"})\n"
            "Assistant: Answer: summarize totals + any notable shifts.\n\n"
            "User: p95 latency yesterday vs last week?\n"
            "Assistant: (call get_latency_trends {since:\"7 days ago\", until:\"now\", granularity:\"day\"})\n"
            "Assistant: Answer: compare yesterday to prior days.\n"
        )

    return (
        "Examples (ReAct style):\n"
        "User: Some new question that doesn't match known patterns\n"
        "Assistant: pick the closest domain tools first; only then safe_sql_query for custom joins/aggregations.\n"
    )

def react_system_prompt_all() -> str:
    """Holistic ReAct system prompt (no category classification needed).

    This prompt includes guidance + examples for ALL categories. The model should
    pick the relevant section(s) based on the user's query.
    """

    blocks: list[str] = []

    blocks.append(
        "\n".join(
            [
                "You are an operator-facing ops debugging agent.",
                "You work in a ReAct loop: call tools, read results, repeat.",
                "You MAY call a SEQUENCE of tools in a single step if it is efficient (e.g., incidents + deployments).",
                "When you have enough evidence, stop calling tools and write a concise final answer.",
                "If evidence is insufficient, say what is missing and propose the next tool call(s) with args.",
                "Do NOT output chain-of-thought.",
            ]
        )
    )

    blocks.append("Schema context (for SQL edge cases):\n" + SCHEMA_INTRO)
    blocks.append("Tool glossary:\n" + TOOL_GLOSSARY)

    blocks.append(
        "\n".join(
            [
                "Category guidance (use whichever matches the query):",
                "STATUS:\n" + _category_guidance("STATUS"),
                "LOOKUP:\n" + _category_guidance("LOOKUP"),
                "INVESTIGATE:\n" + _category_guidance("INVESTIGATE"),
                "TRENDS:\n" + _category_guidance("TRENDS"),
                "NOVEL:\n" + _category_guidance("NOVEL"),
            ]
        )
    )

    blocks.append(
        "\n".join(
            [
                "Examples (ReAct style, use as patterns):",
                _react_examples("STATUS"),
                _react_examples("LOOKUP"),
                _react_examples("INVESTIGATE"),
                _react_examples("TRENDS"),
                _react_examples("NOVEL"),
            ]
        )
    )

    return "\n\n".join(blocks).strip() + "\n"


def _category_guidance(category: Category) -> str:
    if category == "STATUS":
        return (
            "- Goal: current status/health/incidents/simple counts.\n"
            "- Prefer get_active_incidents + get_deployment_status.\n"
            "- Use safe_sql_query only for counts/group-bys not provided by tools.\n"
        )
    if category == "LOOKUP":
        return (
            "- Goal: drill down on a specific request/user.\n"
            "- If req_... present: get_request_detail(request_id=...).\n"
            "- If user_... present: get_user_context(user_id=...).\n"
            "- Optionally: get_recent_requests(user_id=..., limit=20) for supporting evidence.\n"
        )
    if category == "INVESTIGATE":
        return (
            "- Goal: root-cause / why.\n"
            "- Usually start with incidents + deployment health.\n"
            "- Then choose: get_recent_requests (examples) or get_latency_trends/get_quality_summary (aggregates) or get_request_detail (single request).\n"
        )
    if category == "TRENDS":
        return (
            "- Goal: time-based aggregates/comparisons.\n"
            "- request counts/cost => get_request_volume\n"
            "- latency/error_rate => get_latency_trends\n"
            "- quality => get_quality_summary\n"
        )
    return (
        "- Goal: unknown/novel.\n"
        "- Start with the closest domain tools first; only then safe_sql_query for custom joins/aggregations.\n"
    )

