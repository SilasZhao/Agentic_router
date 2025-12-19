from __future__ import annotations

import os
import tempfile

from src.db.connection import connect, fetch_one
from src.db.seed import SeedConfig, seed


def test_seed_creates_expected_core_counts() -> None:
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "context.db")
        # Keep test runtime small while exercising schema + determinism.
        seed(db_path, cfg=SeedConfig(rng_seed=42, days=2, requests_per_user_per_day=10, write_report=False))

        conn = connect(db_path)
        try:
            tiers = fetch_one(conn, "SELECT COUNT(*) AS c FROM tiers")
            models = fetch_one(conn, "SELECT COUNT(*) AS c FROM models")
            backends = fetch_one(conn, "SELECT COUNT(*) AS c FROM backends")
            deployments = fetch_one(conn, "SELECT COUNT(*) AS c FROM deployments")
            dep_state = fetch_one(conn, "SELECT COUNT(*) AS c FROM deployment_state_current")
            users = fetch_one(conn, "SELECT COUNT(*) AS c FROM users")
            incidents = fetch_one(conn, "SELECT COUNT(*) AS c FROM incidents")
            requests = fetch_one(conn, "SELECT COUNT(*) AS c FROM requests")
            assert tiers["c"] == 3
            assert models["c"] == 10
            assert backends["c"] == 3
            assert deployments["c"] == 21
            assert dep_state["c"] == 21
            assert users["c"] == 10
            assert incidents["c"] == 8
            assert requests["c"] == 10 * 10 * 2
        finally:
            conn.close()


def test_seed_is_deterministic_for_key_invariants() -> None:
    with tempfile.TemporaryDirectory() as td:
        db1 = os.path.join(td, "a.db")
        db2 = os.path.join(td, "b.db")
        cfg = SeedConfig(rng_seed=123, days=2, requests_per_user_per_day=8, write_report=False)
        seed(db1, cfg=cfg)
        seed(db2, cfg=cfg)

        c1 = connect(db1)
        c2 = connect(db2)
        try:
            # Same request ids at ends
            first1 = fetch_one(c1, "SELECT id FROM requests ORDER BY id ASC LIMIT 1")
            first2 = fetch_one(c2, "SELECT id FROM requests ORDER BY id ASC LIMIT 1")
            last1 = fetch_one(c1, "SELECT id FROM requests ORDER BY id DESC LIMIT 1")
            last2 = fetch_one(c2, "SELECT id FROM requests ORDER BY id DESC LIMIT 1")
            assert first1 == first2
            assert last1 == last2

            # Same number of quality scores for same seed/config
            q1 = fetch_one(c1, "SELECT COUNT(*) AS c FROM quality_scores")
            q2 = fetch_one(c2, "SELECT COUNT(*) AS c FROM quality_scores")
            assert q1["c"] == q2["c"]
        finally:
            c1.close()
            c2.close()

