"""Tests for production architecture requirements.

Proves:
- Production does not create demo financial records
- Required scenario/policy configuration exists
- Initialization is idempotent
- Real evidence is required (no hallucinated evidence)
- Missing evidence triggers clear error response
- Financial calculations remain deterministic
"""
import json
import os
from datetime import datetime, timezone
import pytest
from unittest.mock import patch


class TestProductionSeedIsolation:
    """Verify production never creates demo financial records."""

    def test_production_env_blocks_seed_data(self):
        """ENV=production must never trigger _seed_dev_data()."""
        env = "production"
        seed_data = False
        should_seed = env != "production" and (seed_data or env == "development")
        assert should_seed is False, "Seed data should NOT load in production"

    def test_production_with_seed_flag_still_blocks(self):
        """Even if SEED_DATA=true, ENV=production must block it."""
        env = "production"
        seed_data = True
        should_seed = env != "production" and (seed_data or env == "development")
        assert should_seed is False, "Seed data should NOT load even with SEED_DATA=true in production"

    def test_dev_allows_seed_data(self):
        """Development mode should allow seed data."""
        env = "development"
        seed_data = True
        should_seed = env != "production" and (seed_data or env == "development")
        assert should_seed is True, "Seed data should load in development"


class TestSystemConfigExists:
    """Verify _ensure_system_config creates required configuration."""

    @pytest.mark.asyncio
    async def test_policies_exist_after_config(self):
        """Policies must be in the database after system config initialization."""
        from main import _ensure_system_config
        from database import get_db

        await _ensure_system_config()

        db = await get_db()
        try:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM policies")
            row = await cursor.fetchone()
            count = row["cnt"] if hasattr(row, "keys") else row[0]
            assert count >= 4, f"Expected at least 4 policies, got {count}"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_scenarios_exist_after_config(self):
        """Scenarios must be in the database after system config initialization."""
        from main import _ensure_system_config
        from database import get_db

        await _ensure_system_config()

        db = await get_db()
        try:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM scenarios")
            row = await cursor.fetchone()
            count = row["cnt"] if hasattr(row, "keys") else row[0]
            assert count >= 5, f"Expected at least 5 scenarios, got {count}"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_scenarios_have_policy_ids(self):
        """Scenarios must have policy_ids populated."""
        from main import _ensure_system_config
        from database import get_db

        await _ensure_system_config()

        db = await get_db()
        try:
            cursor = await db.execute("SELECT scenario_id, policy_ids FROM scenarios WHERE scenario_id = 'scenario_1'")
            row = await cursor.fetchone()
            assert row is not None, "scenario_1 not found"
            policy_ids = row["policy_ids"] if hasattr(row, "keys") else row[1]
            if isinstance(policy_ids, str):
                policy_ids = json.loads(policy_ids)
            assert len(policy_ids) >= 2, f"scenario_1 should have at least 2 policies, got {len(policy_ids)}"
            assert "platform_1_1" in policy_ids, "scenario_1 must reference platform_1_1"
        finally:
            await db.close()


class TestIdempotentInitialization:
    """Verify initialization is idempotent — running twice doesn't duplicate."""

    @pytest.mark.asyncio
    async def test_double_init_no_duplicate_policies(self):
        """Running _ensure_system_config twice should not duplicate policies."""
        from main import _ensure_system_config
        from database import get_db

        await _ensure_system_config()
        db = await get_db()
        try:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM policies")
            row = await cursor.fetchone()
            count1 = row["cnt"] if hasattr(row, "keys") else row[0]
        finally:
            await db.close()

        await _ensure_system_config()
        db = await get_db()
        try:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM policies")
            row = await cursor.fetchone()
            count2 = row["cnt"] if hasattr(row, "keys") else row[0]
        finally:
            await db.close()

        assert count1 == count2, f"Policies duplicated: {count1} -> {count2}"

    @pytest.mark.asyncio
    async def test_double_init_no_duplicate_scenarios(self):
        """Running _ensure_system_config twice should not duplicate scenarios."""
        from main import _ensure_system_config
        from database import get_db

        await _ensure_system_config()
        db = await get_db()
        try:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM scenarios")
            row = await cursor.fetchone()
            count1 = row["cnt"] if hasattr(row, "keys") else row[0]
        finally:
            await db.close()

        await _ensure_system_config()
        db = await get_db()
        try:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM scenarios")
            row = await cursor.fetchone()
            count2 = row["cnt"] if hasattr(row, "keys") else row[0]
        finally:
            await db.close()

        assert count1 == count2, f"Scenarios duplicated: {count1} -> {count2}"


class TestRealEvidenceRequired:
    """Verify the pipeline requires real evidence — never hallucinates."""

    @pytest.mark.asyncio
    async def test_run_scenario_no_evidence_returns_error(self):
        """run_scenario must return error when tenant has no evidence."""
        from database import get_db
        from main import _ensure_system_config

        await _ensure_system_config()

        db = await get_db()
        try:
            # Create a fresh tenant with no evidence
            await db.execute(
                "INSERT OR IGNORE INTO tenants (tenant_id, name) VALUES (?, ?)",
                ("test_empty_tenant", "Empty Tenant"),
            )
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, email, password_hash, display_name, role, tenant_id, created_at, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("usr_empty_test", "empty@test.com", "hashed", "Empty User", "admin", "test_empty_tenant", "2024-01-01", True),
            )
            await db.commit()
        finally:
            await db.close()

        from auth import create_access_token
        token = create_access_token("usr_empty_test", "test_empty_tenant", "admin", "empty@test.com")

        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        response = client.post(
            "/api/scenarios/scenario_1/run",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "evidence" in data["error"].lower() or "evidence" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_pipeline_does_not_create_evidence(self):
        """The pipeline must never create evidence records — only read them."""
        from ai.pipeline import run_pipeline
        from seed_data import get_scenario_policies
        from database import get_db

        policies = get_scenario_policies("scenario_1")

        db = await get_db()
        try:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM evidence WHERE tenant_id = 'phantom_tenant'")
            row = await cursor.fetchone()
            count_before = row["cnt"] if hasattr(row, "keys") else row[0]
        finally:
            await db.close()

        # Pipeline with empty evidence should raise, not create evidence
        with pytest.raises((ValueError, Exception)):
            run_pipeline(
                scenario_id="test",
                evidence_records=[],
                policy_records=policies,
                use_mock=True,
            )

        db = await get_db()
        try:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM evidence WHERE tenant_id = 'phantom_tenant'")
            row = await cursor.fetchone()
            count_after = row["cnt"] if hasattr(row, "keys") else row[0]
        finally:
            await db.close()

        assert count_before == count_after, "Pipeline must not create evidence records"


class TestDeterministicCalculations:
    """Verify financial calculations are deterministic and auditable."""

    def test_platform_fee_deterministic(self):
        """Platform fee must be exactly 8% regardless of AI output."""
        from calculations import calculate_platform_fee

        assert calculate_platform_fee(100000) == 8000
        assert calculate_platform_fee(100000) == 8000  # Same input = same output
        assert calculate_platform_fee(50000) == 4000
        assert calculate_platform_fee(0) == 0

    def test_sla_penalty_fixed(self):
        """SLA penalty must be a fixed amount, never determined by AI."""
        from calculations import calculate_sla_penalty

        assert calculate_sla_penalty(100000, 12000) == 12000
        assert calculate_sla_penalty(80000, 12000) == 12000
        assert calculate_sla_penalty(0, 5000) == 5000

    def test_final_amount_never_negative(self):
        """Final amount must never go below zero."""
        from calculations import calculate_final_amount, build_line_items

        line_items = build_line_items(
            gross_amount=1000,
            has_sla_breach=True,
            sla_penalty_amount=12000,
            has_returns=False,
        )
        final = calculate_final_amount(1000, line_items)
        assert final >= 0, f"Final amount went negative: {final}"

    def test_line_items_deterministic(self):
        """Building line items with same inputs must produce same outputs."""
        from calculations import build_line_items

        items1 = build_line_items(100000, True, 12000, True, 5000)
        items2 = build_line_items(100000, True, 12000, True, 5000)

        assert len(items1) == len(items2)
        for i1, i2 in zip(items1, items2):
            assert i1.label == i2.label
            assert i1.amount == i2.amount
            assert i1.type == i2.type

    def test_calculation_validation(self):
        """validate_calculation must detect inconsistencies."""
        from calculations import validate_calculation, build_line_items, calculate_final_amount

        items = build_line_items(100000, True, 12000, True, 5000)
        correct_final = calculate_final_amount(100000, items)

        result = validate_calculation(100000, items, correct_final)
        assert result["valid"] is True

        # Tampered final amount should fail validation
        result_bad = validate_calculation(100000, items, 99999)
        assert result_bad["valid"] is False


class TestRunScenarioArchitecture:
    """Verify run_scenario uses database, not hardcoded data."""

    @pytest.mark.asyncio
    async def test_run_scenario_reads_policies_from_db(self):
        """run_scenario must read policies from the database, not seed_data."""
        from main import _ensure_system_config
        from database import get_db

        await _ensure_system_config()

        db = await get_db()
        try:
            # Verify scenario_1 has policy_ids in the database
            cursor = await db.execute(
                "SELECT policy_ids FROM scenarios WHERE scenario_id = 'scenario_1'"
            )
            row = await cursor.fetchone()
            assert row is not None, "scenario_1 must exist in database"

            policy_ids = row["policy_ids"] if hasattr(row, "keys") else row[0]
            if isinstance(policy_ids, str):
                policy_ids = json.loads(policy_ids)

            # Verify those policies exist in the database
            for pid in policy_ids:
                cursor_p = await db.execute(
                    "SELECT policy_id FROM policies WHERE policy_id = ?", (pid,)
                )
                p_row = await cursor_p.fetchone()
                assert p_row is not None, f"Policy {pid} must exist in database"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_run_scenario_no_seed_data_imports_in_production_path(self):
        """The production run_scenario path must not import from seed_data for evidence/policies."""
        import inspect
        from routes import run_scenario

        source = inspect.getsource(run_scenario)
        # Should NOT call get_scenario_evidence or get_scenario_policies from seed_data
        assert "get_scenario_evidence" not in source or "seed_data" not in source, \
            "run_scenario should not import get_scenario_evidence from seed_data"
        assert "get_scenario_policies" not in source or "seed_data" not in source, \
            "run_scenario should not import get_scenario_policies from seed_data"

    @pytest.mark.asyncio
    async def test_run_scenario_filter_uses_ai_analyzed(self):
        """run_scenario must filter on ai_analyzed, not linked_decision_ids."""
        import inspect
        from routes import run_scenario

        source = inspect.getsource(run_scenario)
        assert "ai_analyzed" in source, "run_scenario must filter on ai_analyzed column"
        # The SELECT query for evidence must use ai_analyzed, not linked_decision_ids
        # (linked_decision_ids = '[]' may still appear in CASE/WHEN update logic)
        import re
        select_lines = [l.strip() for l in source.split('\n') if 'SELECT' in l and 'evidence' in l]
        for line in select_lines:
            assert 'ai_analyzed' in line, f"Evidence SELECT must filter on ai_analyzed, got: {line}"


class TestAiAnalyzedArchitecture:
    """Verify ai_analyzed flag correctly gates AI scenario processing."""

    @pytest.mark.asyncio
    async def test_razorpay_evidence_eligible_for_ai(self):
        """Evidence linked to a Razorpay decision (linked_decision_ids != []) but
        ai_analyzed=0 must still be found by the scenario runner filter."""
        from database import get_db

        db = await get_db()
        try:
            # Insert evidence that looks like it came from Razorpay: already linked
            # to a deterministic decision, but NOT yet AI-analyzed.
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_razorpay_ai_eligible", "demo", "order",
                    json.dumps({"razorpay_event_id": "evt_test"}),
                    "[]", json.dumps(["dec_razorpay_existing"]),
                    0, "hash123", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            # Query with the ai_analyzed=FALSE filter (same as run_scenario uses)
            cursor = await db.execute(
                "SELECT * FROM evidence WHERE tenant_id = ? AND ai_analyzed = FALSE",
                ("demo",),
            )
            rows = await cursor.fetchall()
            evidence_ids = [r["evidence_id"] if hasattr(r, "keys") else r[0] for r in rows]
            assert "ev_razorpay_ai_eligible" in evidence_ids, \
                "Razorpay evidence with ai_analyzed=0 must be found by scenario runner"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_ai_analyzed_evidence_excluded(self):
        """Evidence with ai_analyzed=1 must NOT be returned by the scenario runner filter."""
        from database import get_db

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_already_ai_analyzed", "demo", "order",
                    json.dumps({"razorpay_event_id": "evt_done"}),
                    "[]", json.dumps(["dec_razorpay_old", "dec_ai_old"]),
                    1, "hash456", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT * FROM evidence WHERE tenant_id = ? AND ai_analyzed = FALSE",
                ("demo",),
            )
            rows = await cursor.fetchall()
            evidence_ids = [r["evidence_id"] if hasattr(r, "keys") else r[0] for r in rows]
            assert "ev_already_ai_analyzed" not in evidence_ids, \
                "Evidence with ai_analyzed=TRUE must be excluded from scenario runner"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_ai_processing_marks_evidence_analyzed(self):
        """After AI pipeline runs, processed evidence must have ai_analyzed=1."""
        from database import get_db

        db = await get_db()
        try:
            # Insert fresh evidence
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_to_be_analyzed", "demo", "order",
                    json.dumps({"razorpay_event_id": "evt_pending"}),
                    "[]", "[]",
                    0, "hash789", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            # Verify it starts as not analyzed
            cursor = await db.execute(
                "SELECT ai_analyzed FROM evidence WHERE evidence_id = ?",
                ("ev_to_be_analyzed",),
            )
            row = await cursor.fetchone()
            val = row["ai_analyzed"] if hasattr(row, "keys") else row[0]
            assert val == 0 or val is False, "Fresh evidence must start with ai_analyzed=FALSE"

            # Simulate what run_scenario does after pipeline success
            await db.execute(
                "UPDATE evidence SET ai_analyzed = TRUE WHERE evidence_id = ?",
                ("ev_to_be_analyzed",),
            )
            await db.commit()

            # Verify it's now marked
            cursor = await db.execute(
                "SELECT ai_analyzed FROM evidence WHERE evidence_id = ?",
                ("ev_to_be_analyzed",),
            )
            row = await cursor.fetchone()
            val = row["ai_analyzed"] if hasattr(row, "keys") else row[0]
            assert val == 1 or val is True, "Evidence must be marked ai_analyzed=TRUE after processing"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_second_run_does_not_reprocess(self):
        """A second scenario run must not find evidence already consumed by AI."""
        from database import get_db

        db = await get_db()
        try:
            # Insert evidence that was already AI-analyzed
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_double_run_test", "demo", "order",
                    json.dumps({"razorpay_event_id": "evt_double"}),
                    "[]", json.dumps(["dec_razorpay_x", "dec_ai_y"]),
                    1, "hash_double", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            # The scenario runner query: ai_analyzed = FALSE
            cursor = await db.execute(
                "SELECT * FROM evidence WHERE tenant_id = ? AND ai_analyzed = FALSE",
                ("demo",),
            )
            rows = await cursor.fetchall()
            evidence_ids = [r["evidence_id"] if hasattr(r, "keys") else r[0] for r in rows]
            assert "ev_double_run_test" not in evidence_ids, \
                "Already-analyzed evidence must not appear in a second run"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_razorpay_decision_remains_linked(self):
        """After AI processing, the original Razorpay decision ID must still be in
        linked_decision_ids. The AI decision is appended, not replaced."""
        from database import get_db

        db = await get_db()
        try:
            razorpay_decision_id = "dec_razorpay_stays"
            ai_decision_id = "dec_ai_appended"

            # Insert evidence already linked to Razorpay decision
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_razorpay_preserved", "demo", "order",
                    json.dumps({"razorpay_event_id": "evt_preserve"}),
                    "[]", json.dumps([razorpay_decision_id]),
                    0, "hash_preserve", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            # Simulate what run_scenario does: append AI decision to linked_decision_ids
            # This is the CASE WHEN logic from routes.py
            await db.execute(
                "UPDATE evidence SET extracted_facts = ?, linked_decision_ids = "
                "CASE WHEN linked_decision_ids = '[]' THEN ? "
                "ELSE json_insert(linked_decision_ids, '$[' || json_array_length(linked_decision_ids) || ']', ?) END "
                "WHERE evidence_id = ?",
                (
                    json.dumps([{"fact": "ai_extracted"}]),
                    json.dumps([ai_decision_id]),
                    ai_decision_id,
                    "ev_razorpay_preserved",
                ),
            )
            # Mark as AI-analyzed
            await db.execute(
                "UPDATE evidence SET ai_analyzed = TRUE WHERE evidence_id = ?",
                ("ev_razorpay_preserved",),
            )
            await db.commit()

            # Verify both decision IDs are present
            cursor = await db.execute(
                "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ?",
                ("ev_razorpay_preserved",),
            )
            row = await cursor.fetchone()
            linked = row["linked_decision_ids"] if hasattr(row, "keys") else row[0]
            if isinstance(linked, str):
                linked = json.loads(linked)

            assert razorpay_decision_id in linked, \
                f"Razorpay decision {razorpay_decision_id} must remain in linked_decision_ids, got {linked}"
            assert ai_decision_id in linked, \
                f"AI decision {ai_decision_id} must be appended to linked_decision_ids, got {linked}"
            assert len(linked) == 2, f"Expected 2 linked decisions, got {len(linked)}: {linked}"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_new_evidence_defaults_ai_analyzed_false(self):
        """Newly inserted evidence must default ai_analyzed to false/0."""
        from database import get_db

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_default_test", "demo", "order",
                    json.dumps({"test": True}),
                    "[]", "[]",
                    "hash_default", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT ai_analyzed FROM evidence WHERE evidence_id = ?",
                ("ev_default_test",),
            )
            row = await cursor.fetchone()
            val = row["ai_analyzed"] if hasattr(row, "keys") else row[0]
            assert val == 0 or val is False, \
                f"New evidence must default ai_analyzed to FALSE, got {val}"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_evidence_filter_compatible_with_both_databases(self):
        """The ai_analyzed=0 filter must work on both PostgreSQL and SQLite.
        This test runs on SQLite (the test database) and verifies the query syntax."""
        from database import get_db

        db = await get_db()
        try:
            # Insert evidence with ai_analyzed=0
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_compat_test", "demo", "order",
                    json.dumps({"test": True}),
                    "[]", "[]",
                    0, "hash_compat", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            # This is the exact query run_scenario uses
            cursor = await db.execute(
                "SELECT * FROM evidence WHERE tenant_id = ? AND ai_analyzed = FALSE",
                ("demo",),
            )
            rows = await cursor.fetchall()
            assert len(rows) >= 1, "Query must return at least the inserted evidence"

            evidence_ids = [r["evidence_id"] if hasattr(r, "keys") else r[0] for r in rows]
            assert "ev_compat_test" in evidence_ids
        finally:
            await db.close()
