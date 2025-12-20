from __future__ import annotations

"""ReAct-oriented category prompts.

These prompts are meant for *tool-calling ReAct loops*, not for JSON-plan generation.

They include:
- DB schema intro (for safe_sql_query)
- Tool glossary (what tools mean + key args)
- ReAct-friendly guidance + examples per category
"""

from src.agent.categories import QueryCategory
from src.agent.category_prompts import SCHEMA_INTRO, TOOL_GLOSSARY


def _react_examples(category: QueryCategory) -> str:
    # Keep examples short and explicitly tool-calling oriented.
    if category == QueryCategory.STATUS:
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

    if category == QueryCategory.LOOKUP:
        return (
            "Examples (ReAct style):\n"
            "User: Explain why request req_abc123 was routed to AWS\n"
            "Assistant: (call get_request_detail {request_id: \"req_abc123\"})\n"
            "Assistant: Answer: explain routing_reason_json / backend_id / any errors.\n\n"
            "User: What is user_456's budget status today?\n"
            "Assistant: (call get_user_context {user_id: \"user_456\"})\n"
            "Assistant: Answer: summarize tier, budget used/remaining.\n"
        )

    if category == QueryCategory.INVESTIGATE:
        return (
            "Examples (ReAct style):\n"
            "User: Why are premium users seeing slow responses?\n"
            "Assistant: (call get_active_incidents {})\n"
            "Assistant: (call get_deployment_status {})\n"
            "Assistant: (call get_recent_requests {user_tier:\"premium\", since:\"2 hours ago\", limit:50})\n"
            "Assistant: Answer: cite evidence (latency_ms/ttft_ms), identify affected deployments/backends, suggest next filter if needed.\n"
        )

    if category == QueryCategory.TRENDS:
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
                "STATUS:\n" + _category_guidance(QueryCategory.STATUS),
                "LOOKUP:\n" + _category_guidance(QueryCategory.LOOKUP),
                "INVESTIGATE:\n" + _category_guidance(QueryCategory.INVESTIGATE),
                "TRENDS:\n" + _category_guidance(QueryCategory.TRENDS),
                "NOVEL:\n" + _category_guidance(QueryCategory.NOVEL),
            ]
        )
    )

    blocks.append(
        "\n".join(
            [
                "Examples (ReAct style, use as patterns):",
                _react_examples(QueryCategory.STATUS),
                _react_examples(QueryCategory.LOOKUP),
                _react_examples(QueryCategory.INVESTIGATE),
                _react_examples(QueryCategory.TRENDS),
                _react_examples(QueryCategory.NOVEL),
            ]
        )
    )

    return "\n\n".join(blocks).strip() + "\n"


def _category_guidance(category: QueryCategory) -> str:
    if category == QueryCategory.STATUS:
        return (
            "- Goal: current status/health/incidents/simple counts.\n"
            "- Prefer get_active_incidents + get_deployment_status.\n"
            "- Use safe_sql_query only for counts/group-bys not provided by tools.\n"
        )
    if category == QueryCategory.LOOKUP:
        return (
            "- Goal: drill down on a specific request/user.\n"
            "- If req_... present: get_request_detail(request_id=...).\n"
            "- If user_... present: get_user_context(user_id=...).\n"
            "- Optionally: get_recent_requests(user_id=..., limit=20) for supporting evidence.\n"
        )
    if category == QueryCategory.INVESTIGATE:
        return (
            "- Goal: root-cause / why.\n"
            "- Usually start with incidents + deployment health.\n"
            "- Then choose: get_recent_requests (examples) or get_latency_trends/get_quality_summary (aggregates) or get_request_detail (single request).\n"
        )
    if category == QueryCategory.TRENDS:
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

