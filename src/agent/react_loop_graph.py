from __future__ import annotations

"""Pure ReAct loop agent (two-node graph).

This module provides a separate agent implementation from `src/agent/graph.py`.

Design:
- Exactly two nodes: `plan` -> `execute` -> `plan` (loop)
- The LLM decides whether to call tools or stop and answer
- Maximum iterations enforced (default: 10)
- We only *mention* the predicted query category as guidance (not a forced path)
"""

import json
from dataclasses import dataclass
from typing import Any, Callable, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, StateGraph

from src.agent.categories import QueryCategory
from src.agent.llm import get_executor_llm
from src.agent.react_category_prompts import react_system_prompt_all
from src.context import api as context_api
from src.context.sql_tools import safe_sql_query


class ReactState(TypedDict, total=False):
    query: str
    db_path: str
    step: int
    max_steps: int
    category: QueryCategory
    messages: list[Any]
    tool_results: dict[str, Any]
    tools_used: list[str]
    tool_calls: list[dict[str, Any]]
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


def _recommended_tools_for_category(category: QueryCategory, registry: ToolRegistry) -> list[str]:
    # Kept for backward compatibility of state/trace structure; not used by default.
    return []


def _build_langchain_tools(registry: ToolRegistry) -> list[StructuredTool]:
    tools: list[StructuredTool] = []
    for name, fn in registry.tools.items():
        tools.append(StructuredTool.from_function(fn, name=name, description=(fn.__doc__ or "")))
    return tools


def build_react_graph(
    *,
    registry: ToolRegistry | None = None,
    llm: Any | None = None,
    max_steps: int = 10,
):
    """Build a two-node ReAct loop graph."""

    registry = registry or default_tool_registry()
    llm = llm or get_executor_llm()

    tools = _build_langchain_tools(registry)
    try:
        llm_with_tools = llm.bind_tools(tools)  # type: ignore[attr-defined]
    except Exception as e:
        raise RuntimeError(
            "LLM does not support tool calling (bind_tools). "
            "Use an OpenAI tool-calling model (e.g., gpt-5-nano) for the ReAct graph."
        ) from e

    def _plan(state: ReactState) -> ReactState:
        q = state.get("query", "")
        step = int(state.get("step") or 0)
        cap = int(state.get("max_steps") or max_steps)
        msgs = list(state.get("messages") or [])

        if not msgs:
            sys = SystemMessage(
                content=react_system_prompt_all()
            )
            msgs = [sys, HumanMessage(content=q)]

        if step >= cap:
            # Hard stop: do NOT call the LLM again (it might keep tool-calling).
            used = state.get("tools_used") or []
            return {
                "messages": msgs,
                "response": (
                    f"Max steps reached (max_steps={cap}). "
                    f"Tools used so far: {', '.join(used) if used else '(none)'}. "
                    "I’m stopping to avoid an infinite loop. If you want, increase max_steps or refine the query."
                ),
                "step": step,
                "max_steps": cap,
            }

        ai = llm_with_tools.invoke(msgs)
        msgs.append(ai)

        # If there are tool calls, route to execute. If not, finalize.
        if getattr(ai, "tool_calls", None):
            return {"messages": msgs, "step": step, "max_steps": cap}
        return {"messages": msgs, "response": (getattr(ai, "content", "") or "").strip(), "step": step, "max_steps": cap}

    def _execute(state: ReactState) -> ReactState:
        msgs = list(state.get("messages") or [])
        if not msgs:
            return {}

        last = msgs[-1]
        tool_calls = list(getattr(last, "tool_calls", None) or [])
        if not tool_calls:
            return {}

        db_path = state.get("db_path")
        results: dict[str, Any] = dict(state.get("tool_results") or {})
        used: list[str] = list(state.get("tools_used") or [])
        calls: list[dict[str, Any]] = list(state.get("tool_calls") or [])

        for tc in tool_calls:
            name = tc.get("name")
            args = tc.get("args") or {}
            tool_call_id = tc.get("id")

            fn = registry.tools.get(name)
            if fn is None:
                msgs.append(ToolMessage(content=json.dumps({"error": "unknown_tool"}), tool_call_id=tool_call_id))
                continue

            final_args = dict(args)
            if db_path:
                final_args = {**final_args, "db_path": db_path}

            res = fn(**final_args)
            results[name] = res
            used.append(name)
            calls.append({"tool_name": name, "args": final_args})

            # Provide observation back to the model.
            msgs.append(ToolMessage(content=json.dumps(res, ensure_ascii=False), tool_call_id=tool_call_id))

        step = int(state.get("step") or 0) + 1
        return {
            "messages": msgs,
            "tool_results": results,
            "tools_used": used,
            "tool_calls": calls,
            "step": step,
        }

    g = StateGraph(ReactState)
    g.add_node("plan", _plan)
    g.add_node("execute", _execute)
    g.set_entry_point("plan")

    def _route_after_plan(state: ReactState) -> str:
        if state.get("response"):
            return "end"
        msgs = state.get("messages") or []
        if msgs and getattr(msgs[-1], "tool_calls", None):
            return "execute"
        return "end"

    g.add_conditional_edges("plan", _route_after_plan, {"execute": "execute", "end": END})
    g.add_edge("execute", "plan")
    return g.compile()

