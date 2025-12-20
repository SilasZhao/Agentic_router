from __future__ import annotations

"""LangGraph agent core.

This file wires:
- classification
- known-pattern execution
- a placeholder NOVEL path (to be expanded next)
- formatting

Graph input: {"query": str, "db_path"?: str}
Graph output: {"response": str, ...}
"""

from dataclasses import dataclass
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

import re

from src.agent.classifier import Classification, classify_query
from src.agent.categories import QueryCategory
from src.agent.category_prompts import CATEGORY_PLAN_CONFIG
from src.agent.formatter import format_response
from src.agent.patterns import QueryPattern
from src.context import api as context_api
import json

from src.agent.llm import get_executor_llm, llm_provider
from src.context.sql_tools import safe_sql_query

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool


class AgentState(TypedDict, total=False):
    query: str
    db_path: str
    pattern: QueryPattern
    category: QueryCategory
    is_complex: bool
    plan: dict[str, Any]
    uncertainty: list[str]
    verification_tips: list[str]
    messages: list[Any]
    tool_results: dict[str, Any]
    tools_used: list[str]
    tool_calls: list[dict[str, Any]]
    alerts: list[str]
    draft_answer: str
    response: str


ToolFn = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ToolRegistry:
    tools: dict[str, ToolFn]


def default_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        tools={
            "get_deployment_status": context_api.get_deployment_status,
            "get_active_incidents": context_api.get_active_incidents,
            "get_recent_requests": context_api.get_recent_requests,
            "get_request_detail": context_api.get_request_detail,
            "get_user_context": context_api.get_user_context,
            "get_latency_trends": context_api.get_latency_trends,
            "get_quality_summary": context_api.get_quality_summary,
            "get_request_volume": context_api.get_request_volume,
            "safe_sql_query": safe_sql_query,
        }
    )


def _ops_like(query: str) -> bool:
    q = (query or "").lower()
    signals = [
        "incident",
        "outage",
        "down",
        "degraded",
        "health",
        "status",
        "unhealthy",
        "latency",
        "slow",
        "p95",
        "p50",
        "timeout",
        "ttft",
        "tail",
        "error",
        "error rate",
        "5xx",
        "rate limit",
        "throttle",
        "spike",
        "regression",
        "elevated",
        "anomaly",
        "now",
        "today",
        "yesterday",
        "last",
        "past",
        "since",
        "over the",
        "in the last",
    ]
    return any(s in q for s in signals)


def build_graph(
    *,
    registry: ToolRegistry | None = None,
    classifier_override=None,
    plan_override=None,
    executor_llm=None,
):
    registry = registry or default_tool_registry()
    executor_llm = executor_llm or get_executor_llm()
    # If the underlying LLM supports per-call binding, force JSON mode for planning
    # without forcing JSON mode for response formatting.
    try:
        planner_llm = executor_llm.bind(format="json")  # type: ignore[attr-defined]
    except Exception:
        planner_llm = executor_llm

    def _react_enabled() -> bool:
        # Enable ReAct by default for OpenAI tool-calling models.
        # (Ollama tool calling support varies; keep legacy plan path there.)
        return llm_provider() == "openai"

    def _build_tools_for_subset(tool_subset: list[str]) -> list[StructuredTool]:
        tools: list[StructuredTool] = []
        for name in tool_subset:
            fn = registry.tools.get(name)
            if fn is None:
                continue
            # StructuredTool can infer the schema from the function signature.
            tools.append(StructuredTool.from_function(fn, name=name, description=(fn.__doc__ or "")))
        return tools

    def _classify(state: AgentState) -> AgentState:
        q = state.get("query", "")
        c: Classification = classify_query(q, classifier=classifier_override)
        category = c.category
        is_complex = c.is_complex

        # Keep pattern field for formatting/backwards-compat; derive a representative value.
        if category == QueryCategory.NOVEL or is_complex:
            pattern = QueryPattern.NOVEL
        elif category == QueryCategory.STATUS:
            pattern = QueryPattern.SYSTEM_STATUS
        elif category == QueryCategory.TRENDS:
            pattern = QueryPattern.TRAFFIC_ANALYSIS
        elif category == QueryCategory.INVESTIGATE:
            pattern = QueryPattern.LATENCY_INVESTIGATION
        else:
            # LOOKUP
            pattern = QueryPattern.REQUEST_LOOKUP if "req_" in q.lower() else QueryPattern.USER_LOOKUP

        return {"pattern": pattern, "category": category, "is_complex": is_complex}

    def _execute_known(state: AgentState) -> AgentState:
        q = state["query"]
        category = state["category"]
        db_path = state.get("db_path")

        results: dict[str, Any] = {}
        used: list[str] = []
        calls: list[dict[str, Any]] = []

        def call(tool_name: str, args: dict[str, Any]) -> None:
            fn = registry.tools.get(tool_name)
            if fn is None:
                return
            final_args = dict(args or {})
            if db_path:
                final_args = {**final_args, "db_path": db_path}
            results[tool_name] = fn(**final_args)
            used.append(tool_name)
            calls.append({"tool_name": tool_name, "args": final_args})

        if category == QueryCategory.STATUS:
            # Always both for status.
            call("get_active_incidents", {})
            call("get_deployment_status", {})
        elif category == QueryCategory.LOOKUP:
            # Minimal ID extraction.
            req = re.search(r"\b(req_[A-Za-z0-9]+)\b", q)
            usr = re.search(r"\b(user_[A-Za-z0-9]+)\b", q)
            if req:
                call("get_request_detail", {"request_id": req.group(1)})
            if usr:
                call("get_user_context", {"user_id": usr.group(1)})
                # Optional: include recent requests for the user for context.
                call("get_recent_requests", {"user_id": usr.group(1), "limit": 20})

        return {"tool_results": results, "tools_used": used, "tool_calls": calls}

    def _react_think(state: AgentState) -> AgentState:
        q = state["query"]
        category = state.get("category", QueryCategory.NOVEL)
        cfg = CATEGORY_PLAN_CONFIG.get(category)
        tool_subset = cfg.tool_subset if cfg else sorted(registry.tools.keys())
        system_prompt = cfg.system_prompt if cfg else ""

        tools = _build_tools_for_subset(tool_subset)
        llm_with_tools = executor_llm
        try:
            llm_with_tools = executor_llm.bind_tools(tools)  # type: ignore[attr-defined]
        except Exception:
            # If tool binding isn't supported, caller should not route here.
            pass

        msgs = list(state.get("messages") or [])
        if not msgs:
            msgs = [
                SystemMessage(
                    content=(
                        "You are an ops debugging agent using tools. "
                        "Use tools iteratively (ReAct): decide next step, call a tool, read the result, repeat. "
                        "When you have enough evidence, answer the user concisely.\n\n"
                        f"Category: {category.value}\n"
                        f"Allowed tools: {tool_subset}\n\n"
                        "Category guidance (prompts/examples):\n"
                        f"{system_prompt}\n"
                    )
                ),
                HumanMessage(content=q),
            ]

        ai = llm_with_tools.invoke(msgs)
        msgs.append(ai)

        # If the model produced tool calls, we will execute them in the next node.
        tool_calls = getattr(ai, "tool_calls", None)
        if tool_calls:
            return {"messages": msgs}

        # Otherwise, treat as final answer draft.
        return {"messages": msgs, "draft_answer": (getattr(ai, "content", "") or "").strip()}

    def _react_execute(state: AgentState) -> AgentState:
        db_path = state.get("db_path")
        msgs = list(state.get("messages") or [])
        if not msgs:
            return {}
        last = msgs[-1]
        tool_calls = list(getattr(last, "tool_calls", None) or [])
        if not tool_calls:
            return {}

        results: dict[str, Any] = dict(state.get("tool_results") or {})
        used: list[str] = list(state.get("tools_used") or [])
        calls: list[dict[str, Any]] = list(state.get("tool_calls") or [])

        for tc in tool_calls:
            name = tc.get("name")
            args = tc.get("args") or {}
            fn = registry.tools.get(name)
            if fn is None:
                msgs.append(ToolMessage(content=json.dumps({"error": "unknown_tool"}), tool_call_id=tc.get("id")))
                continue
            final_args = dict(args)
            if db_path:
                final_args = {**final_args, "db_path": db_path}
            res = fn(**final_args)
            results[name] = res
            used.append(name)
            calls.append({"tool_name": name, "args": final_args})
            msgs.append(ToolMessage(content=json.dumps(res, ensure_ascii=False), tool_call_id=tc.get("id")))

        return {"messages": msgs, "tool_results": results, "tools_used": used, "tool_calls": calls}

    def _plan_task(state: AgentState) -> AgentState:
        q = state["query"]
        category = state.get("category", QueryCategory.NOVEL)
        if plan_override is not None:
            plan = plan_override(q)
        else:
            plan = None
            # Try LLM planner (qwen3:8b) first, but keep a deterministic fallback.
            try:
                tool_names = sorted(registry.tools.keys())
                cfg = CATEGORY_PLAN_CONFIG.get(category)
                tool_subset = cfg.tool_subset if cfg else tool_names
                system_prompt = cfg.system_prompt if cfg else ""

                prompt = (
                    "You are a planner for an ops debugging agent.\\n"
                    "You MUST return JSON only with this shape:\\n"
                    "{\\n"
                    "  \"query_intent\": \"ops_investigation\"|\"request_lookup\"|\"user_lookup\"|\"traffic\"|\"schema_docs\"|\"other\",\\n"
                    "  \"steps\": [{\"tool_name\": string, \"args\": object, \"purpose\": string}],\\n"
                    "  \"uncertainties\": [string],\\n"
                    "  \"verification_tips\": [string]\\n"
                    "}\\n\\n"
                    f"Category: {category.value}\\n"
                    f"Allowed tools (subset): {tool_subset}\\n"
                    "Rules:\\n"
                    "- Prefer domain tools over safe_sql_query.\\n"
                    "- If you use safe_sql_query, keep it read-only and limit results.\\n"
                    "- Use minimal args; omit unknown IDs.\\n\\n"
                    "Category system prompt:\\n"
                    f"{system_prompt}\\n\\n"
                    f"Query: {q}\\n"
                )
                msg = planner_llm.invoke(prompt)
                text = getattr(msg, "content", "") or ""
                # Prefer strict JSON parsing; fall back to brace extraction if model
                # emitted extra text.
                try:
                    plan = json.loads(text.strip())
                except Exception:
                    start = text.find("{")
                    end = text.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        plan = json.loads(text[start : end + 1])
            except Exception:
                plan = None

            if not isinstance(plan, dict):
                plan = {
                    "query_intent": "ops_investigation" if _ops_like(q) else "other",
                    "steps": [],
                    "uncertainties": ["Planner unavailable; using fallback plan."],
                    "verification_tips": [],
                }
        return {
            "plan": plan,
            "uncertainty": list(plan.get("uncertainties") or []),
            "verification_tips": list(plan.get("verification_tips") or []),
        }

    def _validate_plan(state: AgentState) -> AgentState:
        q = state["query"]
        plan = state.get("plan") or {}
        steps = list(plan.get("steps") or [])
        category = state.get("category", QueryCategory.NOVEL)

        # Guardrails: tool allowlist + max steps
        allowed = set(registry.tools.keys())
        cfg = CATEGORY_PLAN_CONFIG.get(category)
        if cfg:
            allowed = allowed.intersection(set(cfg.tool_subset))
        filtered: list[dict[str, Any]] = []
        for s in steps:
            name = (s or {}).get("tool_name")
            if name not in allowed:
                continue
            filtered.append({"tool_name": name, "args": (s or {}).get("args") or {}})
        steps = filtered[:10]

        # Ops-like enforcement: ensure early incident + health checks.
        if _ops_like(q) or plan.get("query_intent") == "ops_investigation":
            needed = ["get_active_incidents", "get_deployment_status"]
            existing = [s["tool_name"] for s in steps]
            prefix: list[dict[str, Any]] = []
            for n in needed:
                if n not in existing:
                    prefix.append({"tool_name": n, "args": {}})
            steps = prefix + steps

        plan["steps"] = steps
        return {"plan": plan}

    def _execute_plan(state: AgentState) -> AgentState:
        db_path = state.get("db_path")
        plan = state.get("plan") or {}
        steps = list(plan.get("steps") or [])

        results: dict[str, Any] = {}
        used: list[str] = []
        calls: list[dict[str, Any]] = []

        for s in steps[:10]:
            name = s.get("tool_name")
            fn = registry.tools.get(name)
            if fn is None:
                continue
            final_args = dict(s.get("args") or {})
            if db_path:
                final_args = {**final_args, "db_path": db_path}
            res = fn(**final_args)
            results[name] = res
            used.append(name)
            calls.append({"tool_name": name, "args": final_args})

        return {"tool_results": results, "tools_used": used, "tool_calls": calls}

    def _detect_anomaly(state: AgentState) -> AgentState:
        alerts: list[str] = []
        tr = state.get("tool_results") or {}

        inc = tr.get("get_active_incidents")
        if isinstance(inc, dict) and isinstance(inc.get("count"), int) and inc["count"] > 0:
            alerts.append(f"{inc['count']} active incident(s)")

        dep = tr.get("get_deployment_status")
        if isinstance(dep, dict) and isinstance(dep.get("deployments"), list):
            unhealthy = [d for d in dep["deployments"] if d.get("status") in ("degraded", "down")]
            stale = [d for d in dep["deployments"] if d.get("is_stale") is True]
            if unhealthy:
                alerts.append(f"{len(unhealthy)} unhealthy deployment(s)")
            if stale:
                alerts.append(f"{len(stale)} stale deployment(s)")

        return {"alerts": alerts}

    def _format(state: AgentState) -> AgentState:
        resp = format_response(
            query=state.get("query", ""),
            pattern=state.get("pattern", QueryPattern.NOVEL),
            category=state.get("category"),
            tool_results=state.get("tool_results", {}),
            tools_used=state.get("tools_used", []),
            alerts=state.get("alerts", []),
            uncertainty=state.get("uncertainty", []),
            verification_tips=state.get("verification_tips", []),
            draft_answer=state.get("draft_answer"),
            llm=executor_llm,
        )
        return {"response": resp}

    g = StateGraph(AgentState)
    g.add_node("classify", _classify)
    g.add_node("execute_known", _execute_known)
    g.add_node("plan_task", _plan_task)
    g.add_node("validate_plan", _validate_plan)
    g.add_node("execute_plan", _execute_plan)
    g.add_node("react_think", _react_think)
    g.add_node("react_execute", _react_execute)
    g.add_node("detect_anomaly", _detect_anomaly)
    g.add_node("format", _format)

    g.set_entry_point("classify")

    def _route(state: AgentState) -> str:
        category = state.get("category", QueryCategory.NOVEL)
        # Fast path only for simple STATUS/LOOKUP; INVESTIGATE/TRENDS mimic NOVEL (plan->validate->execute).
        if category in (QueryCategory.STATUS, QueryCategory.LOOKUP) and not state.get("is_complex", False):
            return "execute_known"
        return "react_think" if _react_enabled() else "plan_task"

    g.add_conditional_edges(
        "classify",
        _route,
        {"execute_known": "execute_known", "plan_task": "plan_task", "react_think": "react_think"},
    )
    g.add_edge("execute_known", "detect_anomaly")
    g.add_edge("plan_task", "validate_plan")
    g.add_edge("validate_plan", "execute_plan")
    g.add_edge("execute_plan", "detect_anomaly")
    # ReAct loop: think -> (tool calls?) -> execute -> think; else proceed.
    def _react_route(state: AgentState) -> str:
        msgs = state.get("messages") or []
        if not msgs:
            return "detect_anomaly"
        last = msgs[-1]
        if getattr(last, "tool_calls", None):
            return "react_execute"
        return "detect_anomaly"

    g.add_conditional_edges("react_think", _react_route, {"react_execute": "react_execute", "detect_anomaly": "detect_anomaly"})
    g.add_edge("react_execute", "react_think")
    g.add_edge("detect_anomaly", "format")
    g.add_edge("format", END)

    return g.compile()
