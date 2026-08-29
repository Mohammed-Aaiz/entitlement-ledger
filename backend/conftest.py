"""Shared test fixtures for EntitlementLedger.

Provides:
- Fresh test database (isolated per test)
- Pre-seeded demo data (policies, evidence, scenarios, decisions)
- Authenticated TestClient with a demo admin user
- Helper functions for creating test users and getting tokens
"""
import json
import os
import sys
import pytest
from datetime import datetime
from pathlib import Path

# Ensure backend dir is on path
sys.path.insert(0, str(Path(__file__).parent))

# Use a test database
os.environ["EL_DB_PATH"] = str(Path(__file__).parent / "test_entitlement.db")
os.environ["SEED_DATA"] = "true"
os.environ["ENV"] = "development"
os.environ["TESTING"] = "true"

import database
from auth import create_access_token, hash_password


@pytest.fixture(autouse=True)
def fresh_db():
    """Create a fresh database before each test and clean up after."""
    import asyncio

    # Remove old test db
    db_path = database.DB_PATH
    if os.path.exists(db_path):
        os.remove(db_path)

    # Initialize
    loop = asyncio.new_event_loop()
    loop.run_until_complete(database.init_db())

    # Seed demo data
    loop.run_until_complete(_seed_test_data())
    loop.close()

    yield

    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)


async def _seed_test_data():
    """Seed the test database with demo tenant, user, policies, evidence, decisions."""
    import hashlib
    from seed_data import (
        POLICY_RECORDS, EVIDENCE_RECORDS,
        get_all_decisions,
    )
    from auth import hash_password

    db = await database.get_db()
    try:
        # Create demo tenant
        await db.execute(
            "INSERT OR IGNORE INTO tenants (tenant_id, name) VALUES (?, ?)",
            ("demo", "Demo Organization"),
        )

        # Create demo admin user (password: "test1234")
        demo_hash = hash_password("test1234")
        now = datetime.utcnow().isoformat()
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, email, password_hash, display_name, role, tenant_id, created_at, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("usr_test_admin", "test@demo.ledger", demo_hash, "Test Admin", "admin", "demo", now, True),
        )

        # Load policies
        for p in POLICY_RECORDS:
            await db.execute(
                "INSERT OR IGNORE INTO policies (policy_id, version, clause_text, effective_date) VALUES (?, ?, ?, ?)",
                (p["policy_id"], p["version"], p["clause_text"], p["effective_date"]),
            )

        # Load evidence (scoped to demo tenant)
        for ev in EVIDENCE_RECORDS:
            extracted = ev.get("extracted_facts", "[]")
            linked = ev.get("linked_decision_ids", "[]")
            await db.execute(
                "INSERT OR IGNORE INTO evidence (evidence_id, tenant_id, source_type, raw_content, extracted_facts, linked_decision_ids, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ev["evidence_id"], "demo", ev["source_type"], ev["raw_content"],
                 extracted if isinstance(extracted, str) else json.dumps(extracted),
                 linked if isinstance(linked, str) else json.dumps(linked),
                 datetime.now().isoformat()),
            )

        # Load scenarios
        scenarios = [
            ("scenario_1", "Return + SLA Breach", "Multi-source decision reconstruction", "completed"),
            ("scenario_2", "Late Delivery Only", "SLA penalty only", "completed"),
            ("scenario_3", "Complaint Without Penalty", "No additional deduction", "completed"),
            ("scenario_4", "Multiple Seller Decisions", "Decision history", "completed"),
            ("scenario_5", "Tampered Decision", "Integrity chain broken", "completed"),
        ]
        for sid, name, desc, status in scenarios:
            await db.execute(
                "INSERT OR IGNORE INTO scenarios (scenario_id, name, description, status) VALUES (?, ?, ?, ?)",
                (sid, name, desc, status),
            )

        # Load seeded decisions
        decisions = get_all_decisions()
        for d in decisions:
            await db.execute(
                "INSERT OR IGNORE INTO decisions "
                "(decision_id, tenant_id, entity_type, entity_id, gross_amount, line_items, final_amount, "
                "policy_version_id, approver_id, approved_at, model_output, prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    d["decision_id"], "demo", d["entity_type"], d["entity_id"],
                    d["gross_amount"], json.dumps(d["line_items"]),
                    d["final_amount"], d["policy_version_id"],
                    d["approver_id"], d["approved_at"],
                    json.dumps(d.get("model_output", {})),
                    d["prev_decision_hash"], d["decision_hash"],
                    d["created_at"], d.get("status", "APPROVED"),
                ),
            )

        await db.commit()
    finally:
        await db.close()


def get_test_token(user_id: str = "usr_test_admin", tenant_id: str = "demo", role: str = "admin") -> str:
    """Generate a valid JWT for testing."""
    return create_access_token(user_id, tenant_id, role, "test@demo.ledger")


@pytest.fixture
def token() -> str:
    """Get a valid auth token for the demo admin user."""
    return get_test_token()


@pytest.fixture
def auth_headers(token: str) -> dict:
    """Get authorization headers for API requests."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client():
    """Get a FastAPI TestClient."""
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)


@pytest.fixture
def auth_client(client, auth_headers):
    """Get a TestClient that sends auth headers with every request."""

    class AuthClient:
        """Wrapper that adds auth headers to all requests."""

        def __init__(self, _client, _headers):
            self._client = _client
            self._headers = _headers

        def get(self, url, **kwargs):
            headers = kwargs.pop("headers", {})
            headers.update(self._headers)
            return self._client.get(url, headers=headers, **kwargs)

        def post(self, url, **kwargs):
            headers = kwargs.pop("headers", {})
            headers.update(self._headers)
            return self._client.post(url, headers=headers, **kwargs)

        def put(self, url, **kwargs):
            headers = kwargs.pop("headers", {})
            headers.update(self._headers)
            return self._client.put(url, headers=headers, **kwargs)

        def delete(self, url, **kwargs):
            headers = kwargs.pop("headers", {})
            headers.update(self._headers)
            return self._client.delete(url, headers=headers, **kwargs)

    return AuthClient(client, auth_headers)
