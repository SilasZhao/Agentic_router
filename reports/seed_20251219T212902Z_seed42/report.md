# Seed report

- DB: `/Users/zhaosongyan/Desktop/Agentic_router/data/context.db`
- Window: `2025-12-04T21:29:02Z` → `2025-12-19T21:29:02Z`
- Requests/user/day: **1000**
- Days: **15**
- Total requests: **150000**
- Quality coverage: **0.6** (actual rows: 90138)
- Snapshot computed from last **300s** of requests

## Table counts

- tiers: 3
- models: 3
- backends: 3
- deployments: 21
- users: 10
- incidents: 8
- requests: 150000
- quality_scores: 90138

## Current snapshot (deployment_state_current)

- `claude-3-haiku/aws` status=healthy samples=0 p50=Nonems p95=Nonems err=0.0 timeout=0.0 ttft_p50=None decode_p50=None
- `claude-3-haiku/k8s` status=healthy samples=2 p50=4988ms p95=4988ms err=0.0 timeout=0.0 ttft_p50=133 decode_p50=72.64
- `claude-3-opus/aws` status=healthy samples=5 p50=9866ms p95=16228ms err=0.0 timeout=0.0 ttft_p50=141 decode_p50=43.456
- `claude-3-opus/neocloud` status=healthy samples=0 p50=Nonems p95=Nonems err=0.0 timeout=0.0 ttft_p50=None decode_p50=None
- `claude-3-sonnet/aws` status=healthy samples=2 p50=3230ms p95=3230ms err=0.0 timeout=0.0 ttft_p50=161 decode_p50=43.34
- `claude-3-sonnet/neocloud` status=healthy samples=0 p50=Nonems p95=Nonems err=0.0 timeout=0.0 ttft_p50=None decode_p50=None
- `gpt-3.5/aws` status=healthy samples=0 p50=Nonems p95=Nonems err=0.0 timeout=0.0 ttft_p50=None decode_p50=None
- `gpt-3.5/k8s` status=healthy samples=2 p50=4725ms p95=4725ms err=0.0 timeout=0.0 ttft_p50=111 decode_p50=67.393
- `gpt-4-mini/aws` status=healthy samples=10 p50=7303ms p95=11660ms err=0.0 timeout=0.0 ttft_p50=110 decode_p50=75.889
- `gpt-4-mini/k8s` status=healthy samples=0 p50=Nonems p95=Nonems err=0.0 timeout=0.0 ttft_p50=None decode_p50=None
- `gpt-4/aws` status=healthy samples=3 p50=14733ms p95=14733ms err=0.0 timeout=0.0 ttft_p50=130 decode_p50=51.579
- `gpt-4/k8s` status=healthy samples=0 p50=Nonems p95=Nonems err=0.0 timeout=0.0 ttft_p50=None decode_p50=None
- `gpt-4/neocloud` status=healthy samples=2 p50=5026ms p95=5026ms err=0.0 timeout=0.0 ttft_p50=145 decode_p50=28.341
- `llama-13b/k8s` status=healthy samples=5 p50=4737ms p95=5425ms err=0.0 timeout=0.0 ttft_p50=156 decode_p50=83.093
- `llama-13b/neocloud` status=healthy samples=0 p50=Nonems p95=Nonems err=0.0 timeout=0.0 ttft_p50=None decode_p50=None
- `llama-70b/k8s` status=healthy samples=0 p50=Nonems p95=Nonems err=0.0 timeout=0.0 ttft_p50=None decode_p50=None
- `llama-70b/neocloud` status=healthy samples=0 p50=Nonems p95=Nonems err=0.0 timeout=0.0 ttft_p50=None decode_p50=None
- `mistral-large/k8s` status=healthy samples=0 p50=Nonems p95=Nonems err=0.0 timeout=0.0 ttft_p50=None decode_p50=None
- `mistral-large/neocloud` status=healthy samples=0 p50=Nonems p95=Nonems err=0.0 timeout=0.0 ttft_p50=None decode_p50=None
- `mixtral-8x7b/k8s` status=healthy samples=2 p50=3893ms p95=3893ms err=0.0 timeout=0.0 ttft_p50=113 decode_p50=47.231
- `mixtral-8x7b/neocloud` status=healthy samples=0 p50=Nonems p95=Nonems err=0.0 timeout=0.0 ttft_p50=None decode_p50=None

## Daily performance summary (p50/p95)

See `report.json` for full details (daily metrics + per-day perf schedule).
