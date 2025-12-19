from __future__ import annotations

"""Known query patterns and their pre-defined tool sequences.

V1 design:
- Classifier returns a QueryPattern or NOVEL
- Known patterns map to deterministic tool sequences
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class QueryPattern(str, Enum):
    SYSTEM_STATUS = "SYSTEM_STATUS"
    ACTIVE_INCIDENTS = "ACTIVE_INCIDENTS"
    LATENCY_INVESTIGATION = "LATENCY_INVESTIGATION"
    QUALITY_INVESTIGATION = "QUALITY_INVESTIGATION"
    ERROR_INVESTIGATION = "ERROR_INVESTIGATION"
    TRAFFIC_ANALYSIS = "TRAFFIC_ANALYSIS"
    REQUEST_LOOKUP = "REQUEST_LOOKUP"
    USER_LOOKUP = "USER_LOOKUP"
    NOVEL = "NOVEL"


@dataclass(frozen=True)
class ToolStep:
    tool_name: str
    args_builder: Callable[[str], dict[str, Any]]


def _no_args(_: str) -> dict[str, Any]:
    return {}


def _extract_request_id(query: str) -> dict[str, Any]:
    # Minimal heuristic: look for token like req_abc123
    import re

    m = re.search(r"\b(req_[A-Za-z0-9]+)\b", query)
    return {"request_id": m.group(1)} if m else {}


def _extract_user_id(query: str) -> dict[str, Any]:
    import re

    m = re.search(r"\b(user_[A-Za-z0-9]+)\b", query)
    return {"user_id": m.group(1)} if m else {}


PATTERN_TOOL_SEQUENCES: dict[QueryPattern, list[ToolStep]] = {
    # We keep fixed sequences only for the simplest fast paths.
    # INVESTIGATE/TRENDS now go through plan->validate->execute for flexibility.
    QueryPattern.SYSTEM_STATUS: [
        ToolStep("get_active_incidents", _no_args),
        ToolStep("get_deployment_status", _no_args),
    ],
    QueryPattern.REQUEST_LOOKUP: [ToolStep("get_request_detail", _extract_request_id)],
    QueryPattern.USER_LOOKUP: [
        ToolStep("get_user_context", _extract_user_id),
        ToolStep("get_recent_requests", _extract_user_id),
    ],
}
