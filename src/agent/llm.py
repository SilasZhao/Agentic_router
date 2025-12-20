from __future__ import annotations

"""LLM factory for the ReAct agent.

This repo now defaults to the ReAct loop agent. The older multi-node agent is
kept under `src/agent/unsuccessful/`.

Env vars:
- LLM_PROVIDER: "openai" (default) or "ollama"

OpenAI:
- OPENAI_API_KEY (required)
- OPENAI_BASE_URL (optional)
- OPENAI_EXECUTOR_MODEL (default: gpt-5-nano)

Ollama (optional/local):
- OLLAMA_BASE_URL (optional)
- OLLAMA_EXECUTOR_MODEL (default: qwen3:8b)
"""

import os
from typing import Any


def llm_provider() -> str:
    return (os.getenv("LLM_PROVIDER") or "openai").strip().lower()


def get_executor_llm() -> Any:
    """Executor LLM used by the ReAct loop."""
    provider = llm_provider()

    if provider == "openai":
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

    if provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except Exception as e:  # pragma: no cover
            raise RuntimeError("Missing dependency: langchain-ollama. Install requirements.txt.") from e

        return ChatOllama(
            base_url=os.getenv("OLLAMA_BASE_URL"),
            model=os.getenv("OLLAMA_EXECUTOR_MODEL", "qwen3:8b"),
            reasoning=False,
            temperature=0,
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
