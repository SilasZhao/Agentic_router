from __future__ import annotations

import pytest

# The previous non-ReAct agent was moved to src/agent/unsuccessful/.
pytest.skip("Legacy agent tests (moved to src/agent/unsuccessful/).", allow_module_level=True)

import os
import tempfile

from src.agent.graph import build_graph
from src.agent.categories import QueryCategory
from src.agent.classifier import Classification
from src.agent.patterns import QueryPattern
from src.db.seed import SeedConfig, seed


def _seed_small_db() -> str:
    td = tempfile.TemporaryDirectory()
    db_path = os.path.join(td.name, "context.db")
    seed(db_path, cfg=SeedConfig(rng_seed=42, days=2, requests_per_user_per_day=10, write_report=False))
    _seed_small_db._td = td  # type: ignore[attr-defined]
    return db_path


def test_graph_known_system_status_executes_tools_and_formats() -> None:
    db_path = _seed_small_db()

    def cls(_: str) -> Classification:
        return Classification(category=QueryCategory.STATUS, is_complex=False)

    class DummyMsg:
        def __init__(self, content: str):
            self.content = content

    class DummyLLM:
        def invoke(self, *_args, **_kwargs):
            return DummyMsg("System looks fine based on the latest snapshot.")

    app = build_graph(classifier_override=cls, executor_llm=DummyLLM())
    out = app.invoke({"query": "system status?", "db_path": db_path})

    assert out["category"] == QueryCategory.STATUS
    assert out["category"].value == "STATUS"
    assert "tool_results" in out
    assert "get_active_incidents" in out["tool_results"]
    assert "get_deployment_status" in out["tool_results"]

    resp = out["response"]
    assert "System looks fine" in resp
    assert "Alerts:" in resp
    assert "Active incidents" in resp
    assert "Deployments" in resp
    assert "Tools used" in resp


def test_graph_novel_ops_like_forces_critical_checks() -> None:
    db_path = _seed_small_db()

    def cls(_: str) -> Classification:
        return Classification(category=QueryCategory.NOVEL, is_complex=True)

    class DummyLLM:
        def invoke(self, *_args, **_kwargs):
            raise RuntimeError("not used for ops-like enforcement when fallback plan triggers")

    app = build_graph(classifier_override=cls, executor_llm=DummyLLM())
    out = app.invoke({"query": "Why is latency spiking now?", "db_path": db_path})

    # NOVEL path should force critical checks for ops-like queries.
    assert out["category"] == QueryCategory.NOVEL
    assert "get_active_incidents" in out.get("tool_results", {})
    assert "get_deployment_status" in out.get("tool_results", {})
    assert out["response"].startswith("[NOVEL QUERY]")


def test_graph_novel_plan_override_is_validated_and_executed() -> None:
    db_path = _seed_small_db()

    def cls(_: str) -> Classification:
        return Classification(category=QueryCategory.NOVEL, is_complex=True)

    def plan(_: str) -> dict:
        # Intentionally omit critical checks; validator should prepend them.
        return {
            "query_intent": "ops_investigation",
            "steps": [{"tool_name": "get_request_volume", "args": {"group_by": "tier"}}],
            "uncertainties": ["test uncertainty"],
            "verification_tips": [],
        }

    # Pass a dummy LLM so tests don't require a running Ollama server.
    class DummyLLM:
        def invoke(self, *_args, **_kwargs):
            raise RuntimeError("should not be called when plan_override is provided")

    app = build_graph(classifier_override=cls, plan_override=plan, executor_llm=DummyLLM())
    out = app.invoke({"query": "Investigate something odd", "db_path": db_path})
    assert out["category"] == QueryCategory.NOVEL
    assert "Uncertainties:" in out["response"]
    assert "- test uncertainty" in out["response"]

    # Validator should prepend critical checks.
    assert out["tools_used"][:2] == ["get_active_incidents", "get_deployment_status"]
    assert "get_request_volume" in out["tool_results"]


def test_latest_request_question_is_answered_in_natural_language() -> None:
    db_path = _seed_small_db()

    def cls(_: str) -> Classification:
        return Classification(category=QueryCategory.NOVEL, is_complex=True)

    def plan(_: str) -> dict:
        return {
            "query_intent": "other",
            "steps": [{"tool_name": "get_recent_requests", "args": {"limit": 1}}],
            "uncertainties": [],
            "verification_tips": [],
        }

    class DummyMsg:
        def __init__(self, content: str):
            self.content = content

    class DummyLLM:
        def invoke(self, *_args, **_kwargs):
            return DummyMsg("The latest request is `req_foo` at `2024-01-01T00:00:00Z`.")

    app = build_graph(classifier_override=cls, plan_override=plan, executor_llm=DummyLLM())
    out = app.invoke({"query": "In the database, what is the latest request?", "db_path": db_path})
    resp = out["response"]
    assert "latest request" in resp.lower()
    assert "req_" in resp
