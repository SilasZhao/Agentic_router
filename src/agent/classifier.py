from __future__ import annotations

"""Query classifier.

V1: local Ollama classification into *coarse categories* + a complexity flag.

This module is written to be testable:
- `classify_query` accepts an optional callable override.
"""

import json
from dataclasses import dataclass
from typing import Callable

from src.agent.categories import QueryCategory
from src.agent.category_prompts import classifier_fewshot_block
from src.agent.llm import get_classifier_llm


@dataclass(frozen=True)
class Classification:
    category: QueryCategory
    is_complex: bool


ClassifierFn = Callable[[str], Classification]


def classify_query(query: str, *, classifier: ClassifierFn | None = None) -> Classification:
    """Classify query into a (category, is_complex) pair.

    If classifier is provided, it is used (for tests or custom routing).
    Otherwise we try Ollama first and fall back to a deterministic heuristic.
    """

    if classifier is not None:
        return classifier(query)

    fewshot = classifier_fewshot_block()

    # Prefer Ollama classifier; fall back to heuristic if unavailable.
    try:
        llm = get_classifier_llm()
        allowed = [c.value for c in QueryCategory]
        prompt = (
            "You are a query classifier for an ops debugging agent.\\n"
            "Output JSON only.\\n"
            "Return JSON only with this exact shape:\\n"
            "{\"category\":\"<one of allowed>\",\"is_complex\":true|false}\\n"
            f"Allowed categories: {allowed}\\n\\n"
            "Definition: is_complex=true if the query is compound, comparative, asks for root-cause, asks for recommendations, or needs multi-step reasoning.\\n\\n"
            "Few-shot examples:\\n"
            f"{fewshot}\\n"
            f"Q: {query}\\n"
            "A:\\n"
        )
        msg = llm.invoke(prompt)
        text = getattr(msg, "content", "") or ""
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = json.loads(text[start : end + 1])
            cat = obj.get("category")
            is_complex = obj.get("is_complex")
            if isinstance(cat, str) and cat in allowed and isinstance(is_complex, bool):
                return Classification(category=QueryCategory(cat), is_complex=is_complex)
    except Exception:
        pass

    q = (query or "").lower()
    is_complex = any(k in q for k in ("why", "root cause", "recommend", "vs", "compare", "explain")) or (" and " in q)

    if "req_" in q or "user_" in q:
        return Classification(category=QueryCategory.LOOKUP, is_complex=is_complex)
    if any(k in q for k in ("deployment", "deployments", "incident", "outage", "unhealthy", "healthy", "status")):
        return Classification(category=QueryCategory.STATUS, is_complex=is_complex)
    if any(k in q for k in ("traffic", "volume", "trend", "yesterday", "last week", "last month")):
        return Classification(category=QueryCategory.TRENDS, is_complex=is_complex)
    if any(k in q for k in ("latency", "slow", "p95", "error", "timeout", "quality", "score")):
        return Classification(category=QueryCategory.INVESTIGATE, is_complex=True)

    return Classification(category=QueryCategory.NOVEL, is_complex=True)
