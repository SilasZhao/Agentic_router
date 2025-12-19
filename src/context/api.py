from __future__ import annotations

"""Context-layer domain tool implementations (public API).

This module is the stable entrypoint expected by `docs/TOOLS.md` and by the agent.

Implementation is split into focused modules under `src/context/`:
- `deployments.py`
- `incidents.py`
- `requests.py`
- `users.py`
- `trends.py`

Design:
- Read-only SQL only (guardrailed)
- Return plain dicts/lists (JSON-serializable)
- Consistent error format
"""

from src.context.deployments import get_deployment_status
from src.context.incidents import get_active_incidents
from src.context.requests import get_recent_requests, get_request_detail
from src.context.trends import get_latency_trends, get_quality_summary, get_request_volume
from src.context.users import get_user_context

__all__ = [
    "get_deployment_status",
    "get_active_incidents",
    "get_recent_requests",
    "get_request_detail",
    "get_user_context",
    "get_latency_trends",
    "get_quality_summary",
    "get_request_volume",
]
