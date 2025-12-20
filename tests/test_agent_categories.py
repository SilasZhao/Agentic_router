from __future__ import annotations

import pytest

# The previous non-ReAct agent was moved to src/agent/unsuccessful/.
pytest.skip("Legacy agent tests (moved to src/agent/unsuccessful/).", allow_module_level=True)

from src.agent.categories import QueryCategory, category_from_pattern
from src.agent.category_prompts import CATEGORY_PLAN_CONFIG, CLASSIFIER_FEWSHOT, classifier_fewshot_block
from src.agent.patterns import QueryPattern


def test_category_mapping() -> None:
    assert category_from_pattern(QueryPattern.SYSTEM_STATUS) == QueryCategory.STATUS
    assert category_from_pattern(QueryPattern.ACTIVE_INCIDENTS) == QueryCategory.STATUS
    assert category_from_pattern(QueryPattern.REQUEST_LOOKUP) == QueryCategory.LOOKUP
    assert category_from_pattern(QueryPattern.USER_LOOKUP) == QueryCategory.LOOKUP
    assert category_from_pattern(QueryPattern.LATENCY_INVESTIGATION) == QueryCategory.INVESTIGATE
    assert category_from_pattern(QueryPattern.TRAFFIC_ANALYSIS) == QueryCategory.TRENDS
    assert category_from_pattern(QueryPattern.NOVEL) == QueryCategory.NOVEL


def test_status_category_allows_safe_sql_query() -> None:
    cfg = CATEGORY_PLAN_CONFIG[QueryCategory.STATUS]
    assert "safe_sql_query" in cfg.tool_subset


def test_schema_intro_mentions_core_tables() -> None:
    from src.agent.category_prompts import SCHEMA_INTRO

    for t in ("deployments", "deployment_state_current", "requests", "incidents", "quality_scores", "users"):
        assert t in SCHEMA_INTRO


def test_classifier_fewshot_includes_all_examples() -> None:
    block = classifier_fewshot_block()
    # Ensure every declared few-shot question is present.
    for q, cat, is_complex in CLASSIFIER_FEWSHOT:
        assert f"Q: {q}" in block
        assert f"\"category\":\"{cat.value}\"" in block
