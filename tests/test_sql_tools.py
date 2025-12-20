from __future__ import annotations

import os
import tempfile

from src.context.sql_tools import safe_sql_query
from src.db.seed import SeedConfig, seed


def _seed_small_db() -> str:
    td = tempfile.TemporaryDirectory()
    db_path = os.path.join(td.name, "context.db")
    seed(db_path, cfg=SeedConfig(rng_seed=42, days=2, requests_per_user_per_day=10, write_report=False))
    _seed_small_db._td = td  # type: ignore[attr-defined]
    return db_path


def test_safe_sql_query_rejects_non_select() -> None:
    db_path = _seed_small_db()
    out = safe_sql_query(db_path=db_path, query="DELETE FROM requests")
    assert out.get("error") is True


def test_safe_sql_query_enforces_max_rows_and_has_more() -> None:
    db_path = _seed_small_db()
    out = safe_sql_query(db_path=db_path, query="SELECT id FROM requests ORDER BY id", max_rows=100)
    assert out.get("error") is not True
    assert out["row_count"] == 100
    assert out["has_more"] is True
    assert len(out["rows"]) == 100


def test_safe_sql_query_timeout_interrupts_recursive_cte() -> None:
    db_path = _seed_small_db()
    # Intentionally expensive query; should be interrupted by progress handler.
    out = safe_sql_query(
        db_path=db_path,
        # Use an aggregate so the engine must enumerate many rows before returning.
        query="WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x < 100000000) SELECT COUNT(*) AS c FROM cnt",
        timeout_sec=0.01,
        max_rows=100,
    )
    assert out.get("error") is True


def test_safe_sql_query_writes_audit_log() -> None:
    db_path = _seed_small_db()
    with tempfile.TemporaryDirectory() as td:
        audit_path = os.path.join(td, "audit.jsonl")
        os.environ["SQL_AUDIT_LOG_PATH"] = audit_path
        try:
            out = safe_sql_query(db_path=db_path, query="SELECT id FROM deployments ORDER BY id", max_rows=3)
            assert out.get("error") is not True
            assert os.path.exists(audit_path)
            with open(audit_path, "r", encoding="utf-8") as f:
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
            assert len(lines) >= 1
        finally:
            os.environ.pop("SQL_AUDIT_LOG_PATH", None)
