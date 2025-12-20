# Arcpoint Context Layer

A real-time context system for intelligent inference routing. This system ingests signals from models and backends, tracks health state, and serves queries to enable smart routing decisions.

## Problem

Route ~10K inference requests/day across 10 models and 3 backends. The routing engine needs millisecond-level answers to:

- **"What's healthy now?"** — Which deployments are up, degraded, or down?
- **"Where should this request go?"** — Given user tier, budget, current load, and SLAs
- **"Why did this happen?"** — Debug slow responses, errors, quality drops

## Solution

A **Context Layer** that:
1. Tracks deployment health (model + backend combinations)
2. Stores request decisions with full routing context
3. Correlates async quality scores with routing decisions
4. Exposes tools for an LLM agent to query and interpret


### Architecture (Agent Layer → Context API → Data Layer)

This is the updated (current) version of the original “Arcpoint Context Layer” plan, mapped to the codebase:

```
┌──────────────────────────────────────────-------------┐
│ Agent Layer                                           │
│ - src/main.py (CLI)                                   │
│ - src/agent/react_loop_graph.py (plan↔execute)        │
│ - src/agent/react_category_prompts.py (system prompt) │
│ - src/agent/llm.py (OpenAI/Ollama)                    │
└──────────────────────┬──────────────────-------------─┘
                       │ tool calls (LangChain tools)
                       ▼
┌──────────────────────────────────────────---┐
│ Context API                                 │
│ - src/context/api.py (domain tools)         │
│ - src/context/sql_tools.py (safe_sql_query) │
└──────────────────────┬───────────────────---┘
                       │ SQL (read-only)
                       ▼
┌─────────────────────────────────────────--─┐
│ Data Layer                                 │
│ - src/db/schema.sql (DDL)                  │
│ - src/db/connection.py (db helpers)        │
│ - src/db/seed.py (deterministic generator) │
│ - data/context.db (generated SQLite DB)    │
└──────────────────────────────────────────--┘
```

## Key Concepts

| Concept | Description |
|---------|-------------|
| **Deployment** | Model + backend (e.g., `gpt-4/aws`). Health is tracked per deployment, not per model. |
| **Request Context** | Every request stores *why* it was routed, not just *where*. |
| **Staleness** | Metrics have timestamps; stale data is flagged, not trusted. |
| **Incidents** | Flexible scope: affects a deployment, model, or entire backend. |

## Tech Stack

- **Python 3.12+** — Simple, readable, fast iteration
- **SQLite** — Portable, sufficient for prototype, easy to upgrade
- **LangGraph + LangChain** — Orchestration + tool interfaces
- **LLM (default)** — OpenAI `gpt-5-nano` (tool-calling)
- **LLM (optional)** — local Ollama (model-dependent tool calling support)
- **Tracing (optional)** — LangSmith

## Project Structure

```
├── README.md              # You are here
├── system_req.md          # Original problem statement
├── docs/
│   ├── DESIGN.md          # Architecture decisions & tradeoffs
│   ├── SCHEMA.md          # Database schema (source of truth)
│   ├── TOOLS.md           # Agent tool contracts
│   └── plans/             # Historical design plans / iterations
├── src/
│   ├── db/
│   │   ├── schema.sql     # SQLite DDL
│   │   ├── connection.py  # Database helper
│   │   └── seed.py        # Mock data generator
│   ├── context/
│   │   ├── api.py         # Context query functions (tool impls)
│   │   └── sql_tools.py   # Safe SQL validation/execution helpers
│   ├── agent/
│   │   ├── llm.py                 # LLM factory (OpenAI/Ollama)
│   │   ├── react_loop_graph.py    # ReAct agent (plan <-> execute loop)
│   │   ├── react_category_prompts.py # Holistic ReAct system prompt (schema + tools + examples)
│   │   └── unsuccessful/          # Legacy v1 agent (kept for reference)
│   └── main.py            # Demo entrypoint
├── data/
│   └── context.db         # SQLite database (generated)
└── tests/
    └── ...                # Unit tests for tools + ReAct loop
```

## Plans (design history)

We keep our past design iterations as markdown plans in `docs/plans/` (copied from Cursor’s `.cursor/plans/` so they’re versioned in Git).

- Use these to understand **why** the architecture changed (v1 planner/validator → ReAct loop).
- They are **not** executed by the code; they are design artifacts.

## Quick Start

```bash
# Clone and setup
git clone https://github.com/SilasZhao/Agentic_router.git
cd Agentic_router

# Recommended: project conda env
conda env create -f environment.yml
conda activate agentic_router

# Initialize database with mock data
python src/db/seed.py

# Run the ReAct agent demo (OpenAI)
export LLM_PROVIDER=openai
export OPENAI_API_KEY=your_key_here
export OPENAI_EXECUTOR_MODEL=gpt-5-nano   # optional (default is gpt-5-nano)

python -m src.main --once "Are there any active incidents?"
```

## Agent Design (current): ReAct loop (read-only)

The current agent is a **read-only ReAct loop**:
- **plan**: the LLM decides what tool(s) to call next (or to stop and answer)
- **execute**: run tool calls, append `ToolMessage` observations, loop back to plan
- **max_steps**: hard stop after 10 iterations to prevent infinite loops
- **SQL fallback**: `safe_sql_query` is available for ad-hoc SELECTs with strict guardrails (SELECT-only, LIMIT, timeout, audit)

## Workflow (current)

```
┌──────────────┐
│  User query  │
└──────┬───────┘
       │
       ▼
┌─────────────────────────┐
│ plan (LLM w/ tools)     │
│ - may call 1+ tools     │
│ - or stop and answer    │
└──────────┬──────────────┘
           │ tool_calls?
     ┌─────┴───────┐
     │             │
     ▼             ▼
┌───────────┐   ┌──────────┐
│ execute   │   │   END    │
│ run tools │   │ response │
└─────┬─────┘   └──────────┘
      │
      └─────────────── back to plan (max 10 loops)
```

## CLI tips

- **Show graph**:

```bash
python -m src.main --show-graph
python -m src.main --show-graph-ascii
python -m src.main --show-graph-mermaid
```

- **Dump full ReAct message history** (System/Human/AI/Tool):

```bash
python -m src.main --dump-messages --once "system status?"
```

## Legacy v1 agent (moved)

The earlier classifier/planner/validator agent was moved to `src/agent/unsuccessful/` for reference.
See `src/agent/unsuccessful/README.md` for its structure and notes.

## Design evolution (v1 → current)

This repo iterated from a “correctness-first, schema-aware planner” to a simpler **ReAct loop** as we learned what breaks in practice.

- **Schema design**
  - **v1 intent**: a minimal SQLite schema + deterministic seeding to support the tool contracts in `docs/TOOLS.md`.
  - **current**: a richer schema (see `src/db/schema.sql`) with clear separation between:
    - **current snapshot** state (e.g. `deployment_state_current`)
    - **event history** (e.g. `requests`, `quality_scores`, `incidents`)
  - **staleness** is treated as first-class: tools flag stale data rather than hiding it. We anchor staleness to the latest deployment snapshot timestamp (to avoid other tables making all deployments appear stale).

- **Tool design**
  - **domain tools first**: we implemented a small set of high-signal, stable tools in `src/context/api.py` (contracts in `docs/TOOLS.md`) so the agent doesn’t have to invent joins/logic every time.
  - **read-only guarantees**: tools never mutate the DB; failures return consistent error envelopes.
  - **escape hatch**: `safe_sql_query` exists for edge cases not covered by domain tools, but is heavily guarded (SELECT-only, LIMIT, timeout, audit) in `src/context/sql_tools.py`.

- **System/agent design**
  - **v1**: `classify → (known tools) OR (plan JSON → validate → execute)` with strict schemas and guardrails. This was traceable, but brittle (planner formatting/parsing, overly constrained args).
  - **current**: a **two-node ReAct loop** (`src/agent/react_loop_graph.py`):
    - the model can call **a sequence of tools per step**
    - observations are appended as `ToolMessage`s so the next plan turn sees prior results
    - a hard `max_steps` cap prevents infinite loops
    - the system prompt is holistic and schema-aware (`src/agent/react_category_prompts.py`)

For the original design artifacts and decisions, see `docs/plans/`.

## Example Queries

**Real-Time Health:**
```
"Which deployments are unhealthy right now?"
"What's the status of GPT-4 across all backends?"
"Are there any active incidents?"
```

**Debugging:**
```
"Why are premium users seeing slow responses?"
"What's causing errors on Llama-70b?"
"Explain why request req_abc123 was routed to AWS"
```

**Trends:**
```
"What was p95 latency yesterday vs last week?"
"Which model performs best for summarization tasks?"
"How many requests did we serve by tier this week?"
```

## Mock Data Scenarios


Mock data is generated by `src/db/seed.py` (deterministic, seeded RNG) to populate `data/context.db` with a coherent time window and the scenarios above.

The latest seed report in this repo is available at `reports/seed_20251219T212902Z_seed42/` (see `report.md` / `report.json`).

---

## Future: HITL and RAG

We want the agent to **continuously adapt to our system** and learn from mistakes, but in a **correctness-first** way.

- **HITL (Human-in-the-Loop)**: learnings are only created/promoted when a human flags an answer as incorrect and approves the correction. This avoids “self-updating memory” approaches (e.g., mem0) where an LLM can store incorrect knowledge without review.
- **Why no RAG yet**: we don’t have enough real, verified operator examples. Today, “memory” is mainly the **system prompt**, where we encode scenarios and a few ReAct-style examples.
- **Future RAG plan**:
  - capture an **evidence bundle** for each incorrect answer (query, tool calls/args, tool outputs, final response)
  - human reviews and promotes approved items into:
    - curated scenario/examples (prompt or policies)
    - (future) a vector DB of historical questions + approved playbooks
  - add a lightweight classifier + retrieval step so the agent can fetch similar historical questions and playbooks before entering the ReAct loop

```
┌──────────────┐
│  Operator Q  │
└──────┬───────┘
       │
       ▼
┌──────────────────────-----------┐
│ ReAct agent (tools)             │
│ retrieve ↔ plan ↔ execute loop  │
└──────┬───────────────-----------┘
       │
       ▼
┌──────────────────────────────┐
│ Answer + evidence (tool log) │
└──────┬───────────────────────┘
       │
       ├───────────────(if incorrect)─────────────┐
       │                                          │
       ▼                                          ▼
┌──────────────────────┐                 ┌───────────────────────┐
│ Done (no learning)   │                 │ HITL: correction flow │
└──────────────────────┘                 │ (human approves)      │
                                         └──────────┬────────────┘
                                                    │
                                                    ▼
                                         ┌────────────────────────----------------
                                         │ Promote learnings into                 │
                                         │ - system prompt (scenarios/examples)   │
                                         │ - few-shot library (approved examples) │
                                         │ - policy DB (guardrails/defaults)      │
                                         │ - (future) vector DB (RAG corpus)      │
                                         └──────────┬────────────-----------------
                                                    │
                                                    ▼
                                         ┌────────────────────────┐
                                         │ Next run: retrieve →   │
                                         │ ReAct tools            │
                                         └────────────────────────┘
```

## Documentation

| Doc | Purpose |
|-----|---------|
| `docs/DESIGN.md` | Architecture, entities, tradeoffs |
| `docs/SCHEMA.md` | Database schema (source of truth) |
| `docs/TOOLS.md` | Agent tool contracts |
| `docs/plans/` | Historical design plans / iterations |
| `reports/` | Seed reports for generated datasets |

## License

MIT

