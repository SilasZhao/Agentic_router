from __future__ import annotations

"""CLI entrypoint to run the LangGraph agent.

Usage:
  python -m src.main --db-path data/context.db
  python -m src.main --show-graph
  python -m src.main --once "Are there any active incidents?"

This runs locally with Ollama:
- classifier: qwen3:0.6b (thinking disabled)
- planner/executor: qwen3:8b

Set OLLAMA_BASE_URL / OLLAMA_*_MODEL to override.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

# Allow running directly: `python src/main.py ...`
# When executed as a script, Python puts `.../src` on sys.path, which breaks
# `import src...` imports unless we add the project root.
if __package__ is None or __package__ == "":  # pragma: no cover
    _ROOT = Path(__file__).resolve().parents[1]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from src.agent.react_loop_graph import build_react_graph


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_db_path() -> str:
    return os.getenv("CONTEXT_DB_PATH", str(_project_root() / "data" / "context.db"))


def _print_graph_ascii() -> None:
    app = build_react_graph()
    g = app.get_graph()

    print("\nCURRENT LANGGRAPH (from app.get_graph())\n")
    print("Nodes:")
    for node_id in sorted(g.nodes.keys()):
        print(f"- {node_id}")

    print("\nEdges:")
    for e in g.edges:
        flag = " (conditional)" if getattr(e, "conditional", False) else ""
        print(f"- {e.source} -> {e.target}{flag}")


def _print_graph_draw_ascii() -> None:
    app = build_react_graph()
    g = app.get_graph()
    try:
        print(g.draw_ascii())
    except ImportError as e:
        # LangChain's ASCII graph renderer requires an optional dependency.
        print(str(e))
        print("\nTip: install it with: pip install grandalf")


def _print_graph_mermaid() -> None:
    app = build_react_graph()
    g = app.get_graph()
    print(g.draw_mermaid())


def _dump_messages(out: dict[str, Any]) -> None:
    msgs = out.get("messages")
    if not isinstance(msgs, list) or not msgs:
        print("\n[debug] No messages in output state.\n")
        return
    print("\n[debug] Messages:")
    for i, m in enumerate(msgs):
        cls = m.__class__.__name__
        content = getattr(m, "content", None)
        tool_calls = getattr(m, "tool_calls", None)
        tool_call_id = getattr(m, "tool_call_id", None)
        print(f"- #{i} {cls}")
        if tool_call_id:
            print(f"  tool_call_id: {tool_call_id}")
        if tool_calls:
            print(f"  tool_calls: {tool_calls}")
        if isinstance(content, str) and content.strip():
            print("  content:")
            print("  " + "\n  ".join(content.splitlines()))
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the Arcpoint agent (LangGraph).")
    ap.add_argument("--db-path", default=_default_db_path(), help="Path to context.db")
    ap.add_argument("--dump-messages", action="store_true", help="Print the full ReAct message history after each run")
    ap.add_argument("--show-graph", action="store_true", help="Print the current graph structure")
    ap.add_argument("--show-graph-ascii", action="store_true", help="Draw the graph using LangGraph ASCII renderer")
    ap.add_argument("--show-graph-mermaid", action="store_true", help="Print Mermaid syntax from LangGraph (paste into a Mermaid viewer)")
    ap.add_argument("--once", default=None, help="Run a single query and exit")
    args = ap.parse_args()

    if args.show_graph:
        _print_graph_ascii()
        return 0
    if args.show_graph_ascii:
        _print_graph_draw_ascii()
        return 0
    if args.show_graph_mermaid:
        _print_graph_mermaid()
        return 0

    app = build_react_graph()

    if args.once:
        out = app.invoke({"query": args.once, "db_path": args.db_path})
        print(out.get("response", ""))
        if args.dump_messages:
            _dump_messages(out)
        return 0

    print(f"DB: {args.db_path}")
    print("Enter a question (empty line to quit).")
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            return 0
        out = app.invoke({"query": q, "db_path": args.db_path})
        print(out.get("response", ""))
        if args.dump_messages:
            _dump_messages(out)


if __name__ == "__main__":
    raise SystemExit(main())
