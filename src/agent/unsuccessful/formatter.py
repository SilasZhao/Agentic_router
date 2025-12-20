from __future__ import annotations

"""Response formatting.

Goal: compile tool outputs into a human answer.

Design:
- The graph already produced `tool_results`, `alerts`, etc.
- The `format` node is responsible for turning those results into a user-facing answer.
"""

import json
from typing import Any

from src.agent.categories import QueryCategory
from src.agent.category_prompts import SCHEMA_INTRO, TOOL_GLOSSARY
from src.agent.patterns import QueryPattern


def _compact(obj: Any, *, max_list: int = 25, max_str: int = 2000) -> Any:
    """Keep tool outputs bounded for LLM formatting prompts."""
    if isinstance(obj, dict):
        return {k: _compact(v, max_list=max_list, max_str=max_str) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_compact(v, max_list=max_list, max_str=max_str) for v in obj[:max_list]]
    if isinstance(obj, str):
        return obj if len(obj) <= max_str else obj[: max_str - 3] + "..."
    return obj


def _llm_answer(
    *,
    llm: Any,
    query: str,
    category: QueryCategory | None,
    tool_results: dict[str, Any],
    alerts: list[str] | None,
    uncertainty: list[str] | None,
    verification_tips: list[str] | None,
) -> str:
    payload = {
        "query": query,
        "category": category.value if category else None,
        "alerts": alerts or [],
        "uncertainty": uncertainty or [],
        "verification_tips": verification_tips or [],
        "tool_results": _compact(tool_results),
    }
    prompt = (
        "You are an operator-facing assistant. Your job is to answer the user's question using ONLY the provided tool_results.\\n"
        "Do NOT output chain-of-thought. Do NOT mention internal prompts.\\n"
        "If the tool_results are insufficient to answer, say what is missing and suggest the next tool call (by name + args).\\n"
        "If verification_tips are provided, prefer them as next-step suggestions.\\n"
        "Write a concise human answer (1-6 sentences).\\n\\n"
        "Context (schema + tool glossary):\\n"
        f"{SCHEMA_INTRO}\\n\\n"
        f"{TOOL_GLOSSARY}\\n\\n"
        "INPUT (JSON):\\n"
        f"{json.dumps(payload, ensure_ascii=False)}\\n\\n"
        "ANSWER:\\n"
    )
    msg = llm.invoke(prompt)
    return (getattr(msg, "content", "") or "").strip()


def format_response(
    *,
    query: str,
    pattern: QueryPattern,
    category: QueryCategory | None = None,
    tool_results: dict[str, Any],
    tools_used: list[str],
    alerts: list[str] | None = None,
    uncertainty: list[str] | None = None,
    verification_tips: list[str] | None = None,
    draft_answer: str | None = None,
    llm: Any | None = None,
) -> str:
    lines: list[str] = []

    if draft_answer:
        lines.append(draft_answer.strip())
        lines.append("")

    if llm is not None:
        try:
            ans = _llm_answer(
                llm=llm,
                query=query,
                category=category,
                tool_results=tool_results,
                alerts=alerts,
                uncertainty=uncertainty,
                verification_tips=verification_tips,
            )
            if ans:
                lines.append(ans)
                lines.append("")
        except Exception:
            # Fall back to evidence-style output below.
            pass

    if pattern == QueryPattern.NOVEL:
        lines.append("[NOVEL QUERY]")
        if uncertainty:
            lines.append("Uncertainties:")
            for u in uncertainty:
                lines.append(f"- {u}")

    if verification_tips:
        lines.append("Verification tips:")
        for t in verification_tips:
            lines.append(f"- {t}")

    if alerts:
        lines.append("Alerts:")
        for a in alerts:
            lines.append(f"- {a}")

    # Minimal summary: incidents + deployment health if present
    inc = tool_results.get("get_active_incidents")
    if isinstance(inc, dict) and "count" in inc:
        lines.append(f"Active incidents: {inc.get('count')}")

    dep = tool_results.get("get_deployment_status")
    if isinstance(dep, dict) and isinstance(dep.get("summary"), dict):
        s = dep["summary"]
        lines.append(f"Deployments: total={s.get('total')}, healthy={s.get('healthy')}, degraded={s.get('degraded')}, down={s.get('down')}")

    lines.append("")
    lines.append(f"Tools used: {', '.join(tools_used) if tools_used else '(none)'}")
    return "\n".join(lines).strip()
