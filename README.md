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

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Health Checks  │────▶│                  │────▶│  Routing Engine │
│  (mock probes)  │     │   Context Layer  │     │  (queries state)│
└─────────────────┘     │                  │     └─────────────────┘
                        │  ┌────────────┐  │
┌─────────────────┐     │  │  SQLite    │  │     ┌─────────────────┐
│  Request Logs   │────▶│  │  Database  │  │────▶│   LLM Agent     │
│                 │     │  └────────────┘  │     │  (debugging)    │
└─────────────────┘     │                  │     └─────────────────┘
                        │  ┌────────────┐  │
┌─────────────────┐     │  │  Context   │  │     ┌─────────────────┐
│  Quality Scores │────▶│  │  API       │  │────▶│   Operators     │
│  (async)        │     │  └────────────┘  │     │  (dashboards)   │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

## Key Concepts

| Concept | Description |
|---------|-------------|
| **Deployment** | Model + backend (e.g., `gpt-4/aws`). Health is tracked per deployment, not per model. |
| **Request Context** | Every request stores *why* it was routed, not just *where*. |
| **Staleness** | Metrics have timestamps; stale data is flagged, not trusted. |
| **Incidents** | Flexible scope: affects a deployment, model, or entire backend. |

## Tech Stack

- **Python 3.11+** — Simple, readable, fast iteration
- **SQLite** — Portable, sufficient for prototype, easy to upgrade
- **OpenAI API** — Raw tool calling (no LangChain overhead)
- **No frameworks** — Direct Python functions, wrap in FastAPI later if needed

## Project Structure

```
├── README.md              # You are here
├── CLAUDE.md              # AI assistant instructions
├── requirements.md        # Original problem statement
├── docs/
│   ├── DESIGN.md          # Architecture decisions & tradeoffs
│   ├── SCHEMA.md          # Database schema (source of truth)
│   └── TOOLS.md           # Agent tool contracts
├── src/
│   ├── db/
│   │   ├── schema.sql     # SQLite DDL
│   │   ├── connection.py  # Database helper
│   │   └── seed.py        # Mock data generator
│   ├── context/
│   │   ├── api.py         # Context query functions (tool impls)
│   │   └── sql_tools.py   # Safe SQL validation/execution helpers
│   ├── agent/
│   │   ├── tools.py       # OpenAI tool definitions
│   │   ├── llm_client.py  # Thin OpenAI client wrapper
│   │   └── agent.py       # Tool-calling agent loop
│   └── main.py            # Demo entrypoint
├── data/
│   └── context.db         # SQLite database (generated)
└── tests/
    └── test_demo.py       # Demo scenario tests
```

## Quick Start

```bash
# Clone and setup
git clone https://github.com/SilasZhao/Agentic_router.git
cd Agentic_router
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Initialize database with mock data
python src/db/seed.py

# Run the agent demo
export OPENAI_API_KEY=your_key_here
python src/main.py
```

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

The synthetic data tells a story:

| Scenario | What It Tests |
|----------|---------------|
| `llama-70b/neocloud` is DOWN | Unhealthy deployment detection |
| `gpt-4/k8s` is DEGRADED | Degraded vs. down distinction |
| Active incident on `llama-70b/neocloud` | Incident correlation |
| Premium users → `gpt-4/aws` | Tier-based routing visibility |
| Quality drop during incident | Causal debugging |

---

## TODO

### Phase 1: Foundation
- [ ] Create project directory structure (`src/`, `data/`, `tests/`)
- [ ] Implement `src/db/schema.sql` — SQLite DDL from SCHEMA.md
- [ ] Implement `src/db/connection.py` — Database connection helper
- [ ] Add `requirements.txt` with dependencies

### Phase 2: Data Layer
- [ ] Implement `src/db/seed.py` — Mock data generator
  - [ ] Generate 6 deployments with varied health states
  - [ ] Generate 10 users across 3 tiers
  - [ ] Generate ~500 requests over 7 days
  - [ ] Generate ~300 quality scores (60% coverage)
  - [ ] Generate 2 incidents (1 active, 1 resolved)

### Phase 3: Context API
- [ ] Implement `src/context/api.py` — Query functions
  - [ ] `get_deployment_status()` — Current health of all deployments
  - [ ] `get_deployment_details()` — Deep dive on one deployment
  - [ ] `get_active_incidents()` — Current incidents
  - [ ] `get_user_context()` — User tier, budget, usage
  - [ ] `search_requests()` — Historical request lookup
  - [ ] `get_quality_summary()` — Quality trends by model/time

### Phase 4: Agent Integration
- [ ] Implement `src/agent/tools.py` — Claude tool definitions
- [ ] Implement `src/agent/agent.py` — Conversation loop with tool dispatch
- [ ] Implement `src/main.py` — Demo entrypoint with example questions

### Phase 5: Polish
- [ ] Add `tests/test_demo.py` — Verify demo scenarios work
- [ ] Validate all three query categories (health, debugging, trends)
- [ ] Clean up commit history
- [ ] Final README review

---

## Design Decisions

See `docs/DESIGN.md` for detailed rationale. Key choices:

1. **Deployment as unit of health** — GPT-4 on AWS vs GPT-4 on k8s have independent health
2. **Budget is derived** — Computed from `SUM(cost)`, not stored separately
3. **Request context is denormalized** — Fast queries on routing decisions
4. **Staleness is explicit** — API flags old data, doesn't hide it

## Documentation

| Doc | Purpose |
|-----|---------|
| `docs/DESIGN.md` | Architecture, entities, tradeoffs |
| `docs/SCHEMA.md` | Database schema (source of truth) |
| `docs/TOOLS.md` | Agent tool contracts |

## License

MIT

