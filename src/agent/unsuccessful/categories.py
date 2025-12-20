from __future__ import annotations

"""High-level query categories.

These categories are broader than QueryPattern and are intended to drive
category-specific prompts + tool subsets.
"""

from enum import Enum

from src.agent.patterns import QueryPattern


class QueryCategory(str, Enum):
    STATUS = "STATUS"          # health + incidents
    LOOKUP = "LOOKUP"          # request/user lookup
    INVESTIGATE = "INVESTIGATE"  # diagnosis / why
    TRENDS = "TRENDS"          # aggregates over time
    NOVEL = "NOVEL"            # compound/unknown


def category_from_pattern(pattern: QueryPattern) -> QueryCategory:
    if pattern in (QueryPattern.SYSTEM_STATUS, QueryPattern.ACTIVE_INCIDENTS):
        return QueryCategory.STATUS
    if pattern in (QueryPattern.REQUEST_LOOKUP, QueryPattern.USER_LOOKUP):
        return QueryCategory.LOOKUP
    if pattern in (QueryPattern.LATENCY_INVESTIGATION, QueryPattern.QUALITY_INVESTIGATION, QueryPattern.ERROR_INVESTIGATION):
        return QueryCategory.INVESTIGATE
    if pattern in (QueryPattern.TRAFFIC_ANALYSIS,):
        return QueryCategory.TRENDS
    return QueryCategory.NOVEL
