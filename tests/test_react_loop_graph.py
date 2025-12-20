from __future__ import annotations

import os
import tempfile
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from src.agent.react_loop_graph import build_react_graph
from src.db.seed import SeedConfig, seed


def _seed_small_db() -> str:
    td = tempfile.TemporaryDirectory()
    db_path = os.path.join(td.name, "context.db")
    seed(db_path, cfg=SeedConfig(rng_seed=42, days=2, requests_per_user_per_day=10, write_report=False))
    _seed_small_db._td = td  # type: ignore[attr-defined]
    return db_path


class DummyToolLLM:
    """A minimal LLM stub that emits one tool call then a final answer."""

    def __init__(self):
        self._bound = False

    def bind_tools(self, _tools: Any):
        self._bound = True
        return self

    def invoke(self, messages: list[Any]):
        # If we already have at least one tool observation, stop.
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Based on the tools, there is 1 active incident.")
        # Otherwise call a tool.
        return AIMessage(
            content="",
            tool_calls=[{"id": "tc1", "name": "get_active_incidents", "args": {}}],
        )


class DummyMultiToolLLM(DummyToolLLM):
    """Emits two tool calls in one step, then answers."""

    def invoke(self, messages: list[Any]):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Incidents checked and deployment status checked.")
        return AIMessage(
            content="",
            tool_calls=[
                {"id": "tc1", "name": "get_active_incidents", "args": {}},
                {"id": "tc2", "name": "get_deployment_status", "args": {}},
            ],
        )


class DummyInfiniteToolLLM(DummyToolLLM):
    """Always calls a tool, to test max_steps hard stop."""

    def invoke(self, messages: list[Any]):
        # Even if the system says 'do not call tools', this dummy keeps calling;
        # our graph should still terminate by routing based on response presence.
        return AIMessage(content="", tool_calls=[{"id": "tc1", "name": "get_active_incidents", "args": {}}])


def test_react_loop_executes_tool_and_stops() -> None:
    db_path = _seed_small_db()
    app = build_react_graph(llm=DummyToolLLM(), max_steps=10)
    out = app.invoke({"query": "Are there any active incidents?", "db_path": db_path})
    assert "response" in out
    assert "active incident" in (out["response"] or "").lower()
    assert out.get("tools_used") == ["get_active_incidents"]
    assert out.get("tool_calls") and out["tool_calls"][0]["tool_name"] == "get_active_incidents"


def test_react_loop_stops_at_max_steps() -> None:
    db_path = _seed_small_db()
    app = build_react_graph(llm=DummyInfiniteToolLLM(), max_steps=2)
    out = app.invoke({"query": "Keep going forever", "db_path": db_path})
    # We should still produce *some* response at the hard stop.
    assert "response" in out
    assert isinstance(out["response"], str)
    assert "max steps reached" in out["response"].lower()


def test_react_loop_can_execute_multiple_tools_in_one_step() -> None:
    db_path = _seed_small_db()
    app = build_react_graph(llm=DummyMultiToolLLM(), max_steps=10)
    out = app.invoke({"query": "system status?", "db_path": db_path})
    assert "response" in out
    assert "deployment" in out["response"].lower()
    assert out.get("tools_used") == ["get_active_incidents", "get_deployment_status"]
    assert [c["tool_name"] for c in out.get("tool_calls", [])] == ["get_active_incidents", "get_deployment_status"]

