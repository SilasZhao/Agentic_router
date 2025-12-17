# Arcpoint Context Layer

## What Is This?

A context system for intelligent routing. The routing engine queries this system to make decisions about which model/backend to use for each inference request.

Read `requirements.md` for the full problem statement.

## Documentation

**Read these before making changes:**

- `docs/DESIGN.md` — Architecture, entities, tradeoffs
- `docs/SCHEMA.md` — Database schema (source of truth for data model)
- `docs/TOOLS.md` — Agent tool contracts (source of truth for agent capabilities)

## Tech Stack

- **Database:** SQLite
- **Language:** Python 3.11+
- **Agent:** Raw Claude function calling (no LangChain)
- **API:** Python functions (no framework for V1)
- **Timestamps:** RFC3339 UTC (e.g., "2024-01-15T14:30:00Z")

## Project Structure

```
├── CLAUDE.md              # This file
├── requirements.md        # Original problem statement
├── docs/
│   ├── DESIGN.md          # Architecture decisions
│   ├── SCHEMA.md          # Table definitions
│   └── TOOLS.md           # Agent tool contracts
├── src/
│   ├── db/
│   │   ├── schema.sql     # SQLite schema
│   │   ├── connection.py  # Database connection helper
│   │   └── seed.py        # Mock data generator
│   ├── context/
│   │   └── api.py         # Context query functions (implements TOOLS.md)
│   ├── agent/
│   │   ├── tools.py       # Tool definitions for Claude
│   │   └── agent.py       # Agent loop
│   └── main.py            # Demo entrypoint
├── data/
│   └── context.db         # SQLite database (generated)
├── tests/
│   └── test_demo.py       # Demo scenarios
└── README.md              # Submission README
```

## Rules

1. **Check schema before writing DB code** — Always read `docs/SCHEMA.md`
2. **Check tools doc before modifying agent** — Always read `docs/TOOLS.md`
3. **No ORMs** — Use raw SQL for clarity
4. **Tools return dicts** — Agent tools return Python dicts, agent formats for humans
5. **Timestamps are UTC** — All timestamps RFC3339 UTC format
6. **Budget is derived** — Never store `daily_budget_used`; compute from `SUM(requests.cost_usd)`

## Key Concepts

- **Deployment** = model + backend (e.g., "gpt-4/aws")
- **Request** stores routing decision context (the "why", not just "what")
- **Incidents** have flexible scope: deployment, model, or backend level
- **Quality scores** arrive hours after requests (async)
- **Staleness** is computed by API from `updated_at` and `sample_count`

## Mock Data Scenarios

The synthetic data should tell a story that exercises all agent capabilities:

| Scenario | Purpose |
|----------|---------|
| llama-70b/neocloud is DOWN | "Which deployments are unhealthy?" |
| gpt-4/k8s is DEGRADED (high latency) | Degraded ≠ down distinction |
| Active incident on llama-70b/neocloud | Incident correlation |
| Resolved incident from yesterday on gpt-4/k8s | Historical debugging |
| Premium users mostly routed to gpt-4/aws | "Why are premium users slow?" |
| Budget users routed to llama-70b/k8s | Tier-based routing visible |
| Quality scores drop during incident window | "Why did quality drop?" |
| Router version changed mid-week | Version-based debugging |
| One A/B experiment running | Experiment filtering |

**Data volume:**
- 6 deployments
- 10 users (3 premium, 4 standard, 3 budget)
- ~500 requests over 7 days
- ~300 quality scores (60% coverage)
- 2 incidents (1 active, 1 resolved)

## Running

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install anthropic

# Initialize DB with mock data
python src/db/seed.py

# Run agent demo
python src/main.py
```

## Demo Questions to Test

**Category A: Real-Time Health**
```
"Which deployments are unhealthy right now?"
"What's the status of GPT-4 across all backends?"
"Are there any active incidents?"
```

**Category B: Debugging**
```
"Why are premium users seeing slow responses?"
"Explain why request req_abc123 was routed to AWS"
"What's causing errors on Llama-70b?"
```

**Category C: Trends**
```
"What was p95 latency yesterday vs last week?"
"Which model performs best for summarization?"
"How many requests by tier this week?"
```

## Commit Flow

```
Commit 1: Add design docs (DESIGN.md, SCHEMA.md, TOOLS.md, CLAUDE.md)
Commit 2: Add project structure with stubs
Commit 3: Implement schema and connection
Commit 4: Implement mock data generator
Commit 5: Implement context API
Commit 6: Implement agent tools and loop
Commit 7: Add demo script
Commit 8: Add tests and polish README
```
