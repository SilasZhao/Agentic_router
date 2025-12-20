from __future__ import annotations

"""LLM factory helpers.

We use local Ollama for v1:
- classifier: qwen3:0.6b
- executor/planner: qwen3:8b

Env overrides:
- LLM_PROVIDER (ollama|openai) [default: ollama]
- OLLAMA_BASE_URL (optional)
- OLLAMA_CLASSIFIER_MODEL (default: qwen3:0.6b)
- OLLAMA_EXECUTOR_MODEL (default: qwen3:8b)

OpenAI overrides:
- OPENAI_API_KEY (required if LLM_PROVIDER=openai)
- OPENAI_BASE_URL (optional)
- OPENAI_CLASSIFIER_MODEL (default: gpt-5-nano)
- OPENAI_EXECUTOR_MODEL (default: gpt-5-nano)
"""

import os

from langchain_ollama import ChatOllama


def llm_provider() -> str:
    return (os.getenv("LLM_PROVIDER") or "ollama").strip().lower()


def get_ollama_classifier():
    return ChatOllama(
        base_url=os.getenv("OLLAMA_BASE_URL"),
        model=os.getenv("OLLAMA_CLASSIFIER_MODEL", "qwen3:0.6b"),
        # Disable "thinking"/reasoning traces; we want strict JSON classification.
        # reasoning=False,
        temperature=0,
        format="json",
        num_predict=128,
    )


def get_ollama_executor():
    return ChatOllama(
        base_url=os.getenv("OLLAMA_BASE_URL"),
        model=os.getenv("OLLAMA_EXECUTOR_MODEL", "qwen3:8b"),
        # Prefer no chain-of-thought style output in logs/UI.
        reasoning=False,
        temperature=0,
    )


def get_openai_classifier():
    # Lazy import so tests/workflows without this dependency still run.
    try:
        from langchain_openai import ChatOpenAI
    except Exception as e:  # pragma: no cover
        raise RuntimeError("Missing dependency: langchain-openai. Install requirements.txt.") from e

    return ChatOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        model=os.getenv("OPENAI_CLASSIFIER_MODEL", "gpt-5-nano"),
        temperature=0,
        # Force JSON for classifier outputs.
        model_kwargs={"response_format": {"type": "json_object"}},
    )


def get_openai_executor():
    try:
        from langchain_openai import ChatOpenAI
    except Exception as e:  # pragma: no cover
        raise RuntimeError("Missing dependency: langchain-openai. Install requirements.txt.") from e

    return ChatOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        model=os.getenv("OPENAI_EXECUTOR_MODEL", "gpt-5-nano"),
        temperature=0,
    )


def get_classifier_llm():
    if llm_provider() == "openai":
        return get_openai_classifier()
    return get_ollama_classifier()


def get_executor_llm():
    if llm_provider() == "openai":
        return get_openai_executor()
    return get_ollama_executor()
