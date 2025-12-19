from __future__ import annotations

import os
import tempfile
from src.db.connection import connect, fetch_all, fetch_one, init_db


def _schema_path() -> str:
    # tests/ is at project_root/tests; schema is at project_root/src/db/schema.sql
    project_root = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(project_root, "src", "db", "schema.sql")


def test_init_db_creates_tables_and_indexes() -> None:
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        conn = connect(db_path)
        try:
            init_db(conn, schema_path=_schema_path())

            # Tables
            tables = fetch_all(
                conn,
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
            )
            table_names = {t["name"] for t in tables}
            assert {
                "tiers",
                "models",
                "backends",
                "deployments",
                "deployment_state_current",
                "users",
                "requests",
                "incidents",
                "quality_scores",
            } <= table_names

            # Indexes (spot-check a couple)
            indexes = fetch_all(conn, "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name")
            idx_names = {i["name"] for i in indexes}
            assert "idx_requests_created" in idx_names
            assert "idx_incidents_target" in idx_names
        finally:
            conn.close()


def test_fetch_one_and_fetch_all_return_dicts() -> None:
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        conn = connect(db_path)
        try:
            init_db(conn, schema_path=_schema_path())

            # Insert minimal row into tiers/users (required for FK tests later)
            conn.execute(
                "INSERT INTO tiers (id, latency_sla_p95_ms, sla_window_sec, max_error_rate, max_timeout_rate) VALUES (?, ?, ?, ?, ?)",
                ("premium", 500, 300, 0.03, 0.02),
            )
            conn.execute(
                "INSERT INTO users (id, tier_id, daily_budget_usd) VALUES (?, ?, ?)",
                ("user_1", "premium", 10.0),
            )
            conn.commit()

            one = fetch_one(conn, "SELECT id, tier_id FROM users WHERE id = ?", ["user_1"])
            assert one == {"id": "user_1", "tier_id": "premium"}

            many = fetch_all(conn, "SELECT id FROM users ORDER BY id")
            assert many == [{"id": "user_1"}]
        finally:
            conn.close()

