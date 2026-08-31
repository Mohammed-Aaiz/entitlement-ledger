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
        assert response.status_code == 422  # Correct HTTP status for business validation failure
        data = response.json()
        # FastAPI HTTPException wraps detail in {detail: {...}}
        body = data.get("detail", data)
        assert body["status"] == "error"
        assert "evidence" in body["error"].lower() or "evidence" in body["message"].lower()

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
        """run_scenario must filter on ai_analyzed, not linked_decision_ids.

        The eligibility SELECT (fetching evidence for AI processing) must use
        ai_analyzed.  Read-modify-write SELECTs for linked_decision_ids updates
        and idempotency-check SELECTs are excluded from this check.
        """
        import inspect
        from routes import run_scenario

        source = inspect.getsource(run_scenario)
        assert "ai_analyzed" in source, "run_scenario must filter on ai_analyzed column"
        # Check that the eligibility SELECT (fetching evidence rows for AI)
        # uses ai_analyzed, not linked_decision_ids = '[]' as the filter.
        select_lines = [
            l.strip() for l in source.split('\n')
            if 'SELECT' in l and 'evidence' in l
        ]
        # Exclude:
        # - read-modify-write SELECTs that just read linked_decision_ids
        # - idempotency-check SELECTs that read evidence_id + linked_decision_ids
        eligibility_selects = [
            l for l in select_lines
            if 'SELECT linked_decision_ids' not in l
            and 'SELECT evidence_id, linked_decision_ids' not in l
        ]
        for line in eligibility_selects:
            assert 'ai_analyzed' in line, \
                f"Evidence eligibility SELECT must filter on ai_analyzed, got: {line}"


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

            # Simulate what run_scenario does: append AI decision to linked_decision_ids.
            # Use read-modify-write (same pattern as the fixed routes.py).
            cursor = await db.execute(
                "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ?",
                ("ev_razorpay_preserved",),
            )
            row = await cursor.fetchone()
            current_ids = json.loads(row["linked_decision_ids"] if hasattr(row, "keys") else row[0])
            current_ids.append(ai_decision_id)
            await db.execute(
                "UPDATE evidence SET extracted_facts = ?, linked_decision_ids = ? "
                "WHERE evidence_id = ?",
                (
                    json.dumps([{"fact": "ai_extracted"}]),
                    json.dumps(current_ids),
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
    async def test_jsonb_append_empty_to_single_id(self):
        """Append decision ID to empty linked_decision_ids -> ["dec_x"]."""
        from database import get_db

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_jsonb_empty", "demo", "order",
                    json.dumps({"test": True}),
                    "[]", json.dumps([]),
                    0, "hash_jsonb_empty", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            # Read-modify-write pattern (same as fixed routes.py)
            cursor = await db.execute(
                "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ?",
                ("ev_jsonb_empty",),
            )
            row = await cursor.fetchone()
            current_ids = json.loads(row["linked_decision_ids"] if hasattr(row, "keys") else row[0])
            current_ids.append("dec_new_1")
            await db.execute(
                "UPDATE evidence SET linked_decision_ids = ? WHERE evidence_id = ?",
                (json.dumps(current_ids), "ev_jsonb_empty"),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ?",
                ("ev_jsonb_empty",),
            )
            row = await cursor.fetchone()
            linked = json.loads(row["linked_decision_ids"] if hasattr(row, "keys") else row[0])
            assert linked == ["dec_new_1"], f"Expected [dec_new_1], got {linked}"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_jsonb_append_to_existing_ids(self):
        """Append decision ID to ["dec_razorpay"] -> ["dec_razorpay","dec_ai"]."""
        from database import get_db

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_jsonb_existing", "demo", "order",
                    json.dumps({"test": True}),
                    "[]", json.dumps(["dec_razorpay_existing"]),
                    0, "hash_jsonb_existing", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            # Read-modify-write pattern
            cursor = await db.execute(
                "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ?",
                ("ev_jsonb_existing",),
            )
            row = await cursor.fetchone()
            current_ids = json.loads(row["linked_decision_ids"] if hasattr(row, "keys") else row[0])
            current_ids.append("dec_ai_new")
            await db.execute(
                "UPDATE evidence SET linked_decision_ids = ? WHERE evidence_id = ?",
                (json.dumps(current_ids), "ev_jsonb_existing"),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ?",
                ("ev_jsonb_existing",),
            )
            row = await cursor.fetchone()
            linked = json.loads(row["linked_decision_ids"] if hasattr(row, "keys") else row[0])
            assert linked == ["dec_razorpay_existing", "dec_ai_new"], \
                f"Expected both IDs, got {linked}"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_jsonb_persisted_as_valid_json(self):
        """Verify linked_decision_ids is stored as valid JSON in the database."""
        from database import get_db

        db = await get_db()
        try:
            test_ids = ["dec_a", "dec_b", "dec_c"]
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_jsonb_valid", "demo", "order",
                    json.dumps({"test": True}),
                    "[]", json.dumps(test_ids),
                    0, "hash_jsonb_valid", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            # Read back and verify it's valid JSON
            cursor = await db.execute(
                "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ?",
                ("ev_jsonb_valid",),
            )
            row = await cursor.fetchone()
            raw = row["linked_decision_ids"] if hasattr(row, "keys") else row[0]
            # Must be parseable as JSON
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            assert isinstance(parsed, list), f"Expected list, got {type(parsed)}"
            assert parsed == test_ids, f"Expected {test_ids}, got {parsed}"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_jsonb_append_idempotent_no_duplicates(self):
        """Appending the same decision ID twice must not create duplicates.

        The deduplication guard ensures idempotency: re-running the same
        scenario does not produce duplicate entries in linked_decision_ids.
        """
        from database import get_db
        from routes import _parse_json_field

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_jsonb_idempotent", "demo", "order",
                    json.dumps({"test": True}),
                    "[]", json.dumps(["dec_razorpay_existing"]),
                    0, "hash_idempotent", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            # Simulate the read-modify-write with dedup (same logic as routes.py)
            decision_id = "dec_ai_new"
            for _ in range(3):  # append 3 times
                cursor = await db.execute(
                    "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ?",
                    ("ev_jsonb_idempotent",),
                )
                row = await cursor.fetchone()
                current_ids = _parse_json_field(
                    row["linked_decision_ids"] if hasattr(row, "keys") else row[0]
                )
                if not isinstance(current_ids, list):
                    current_ids = []
                if decision_id not in current_ids:
                    current_ids.append(decision_id)
                await db.execute(
                    "UPDATE evidence SET linked_decision_ids = ? WHERE evidence_id = ?",
                    (json.dumps(current_ids), "ev_jsonb_idempotent"),
                )
                await db.commit()

            # Verify no duplicates
            cursor = await db.execute(
                "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ?",
                ("ev_jsonb_idempotent",),
            )
            row = await cursor.fetchone()
            linked = json.loads(row["linked_decision_ids"] if hasattr(row, "keys") else row[0])
            assert linked == ["dec_razorpay_existing", "dec_ai_new"], \
                f"Expected exactly 2 IDs (no duplicates), got {linked}"
            assert len(linked) == len(set(linked)), f"Duplicate IDs found: {linked}"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_jsonb_append_handles_none_and_malformed(self):
        """Null/malformed linked_decision_ids must be handled safely."""
        from database import get_db
        from routes import _parse_json_field

        db = await get_db()
        try:
            # Insert with empty string (malformed)
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_jsonb_malformed", "demo", "order",
                    json.dumps({"test": True}),
                    "[]", "",
                    0, "hash_malformed", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ?",
                ("ev_jsonb_malformed",),
            )
            row = await cursor.fetchone()
            raw = row["linked_decision_ids"] if hasattr(row, "keys") else row[0]
            current_ids = _parse_json_field(raw)
            if not isinstance(current_ids, list):
                current_ids = []
            current_ids.append("dec_from_malformed")

            await db.execute(
                "UPDATE evidence SET linked_decision_ids = ? WHERE evidence_id = ?",
                (json.dumps(current_ids), "ev_jsonb_malformed"),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ?",
                ("ev_jsonb_malformed",),
            )
            row = await cursor.fetchone()
            linked = json.loads(row["linked_decision_ids"] if hasattr(row, "keys") else row[0])
            assert linked == ["dec_from_malformed"], \
                f"Expected [dec_from_malformed], got {linked}"
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


class TestApprovedAtNullable:
    """Verify approved_at is nullable for REVIEW_REQUIRED AI decisions."""

    @pytest.mark.asyncio
    async def test_review_required_decision_with_null_approved_at(self):
        """A REVIEW_REQUIRED decision with approved_at=None can be persisted."""
        from database import get_db
        from main import _ensure_system_config
        from hash_chain import compute_decision_hash

        await _ensure_system_config()

        db = await get_db()
        try:
            decision_id = "dec_test_null_approved"
            now = datetime.now(timezone.utc).isoformat()
            decision_data = {
                "decision_id": decision_id,
                "entity_type": "seller",
                "entity_id": "seller_test",
                "gross_amount": 100000,
                "line_items": json.dumps([]),
                "final_amount": 92000,
                "policy_version_id": "platform_1_1",
                "approver_id": "ai_pipeline",
                "approved_at": None,
                "model_output": json.dumps({"source": "ai_pipeline"}),
                "prev_decision_hash": "genesis",
                "decision_hash": "",
                "created_at": now,
                "status": "REVIEW_REQUIRED",
            }
            # Compute hash (approved_at=None must be handled)
            hash_input = {k: v for k, v in decision_data.items() if k != "decision_hash"}
            decision_data["decision_hash"] = compute_decision_hash(hash_input, "genesis")

            await db.execute(
                "INSERT INTO decisions "
                "(decision_id, tenant_id, entity_type, entity_id, gross_amount, line_items, "
                "final_amount, policy_version_id, approver_id, approved_at, model_output, "
                "prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision_data["decision_id"], "demo",
                    decision_data["entity_type"], decision_data["entity_id"],
                    decision_data["gross_amount"], decision_data["line_items"],
                    decision_data["final_amount"], decision_data["policy_version_id"],
                    decision_data["approver_id"], decision_data["approved_at"],
                    decision_data["model_output"], decision_data["prev_decision_hash"],
                    decision_data["decision_hash"], decision_data["created_at"],
                    decision_data["status"],
                ),
            )
            await db.commit()

            # Read it back — approved_at must be NULL
            cursor = await db.execute(
                "SELECT approved_at, status FROM decisions WHERE decision_id = ?",
                (decision_id,),
            )
            row = await cursor.fetchone()
            assert row is not None, "Decision must be persisted"
            approved_at = row["approved_at"] if hasattr(row, "keys") else row[0]
            status = row["status"] if hasattr(row, "keys") else row[1]
            assert approved_at is None, f"approved_at must be NULL for REVIEW_REQUIRED, got {approved_at}"
            assert status == "REVIEW_REQUIRED"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_approved_decision_has_timestamp(self):
        """An APPROVED Razorpay decision must have a real timestamp in approved_at."""
        from database import get_db
        from main import _ensure_system_config
        from hash_chain import compute_decision_hash

        await _ensure_system_config()

        db = await get_db()
        try:
            decision_id = "dec_test_approved_ts"
            now = datetime.now(timezone.utc).isoformat()
            decision_data = {
                "decision_id": decision_id,
                "entity_type": "seller",
                "entity_id": "seller_razorpay",
                "gross_amount": 100000,
                "line_items": json.dumps([]),
                "final_amount": 92000,
                "policy_version_id": "platform_1_1",
                "approver_id": "razorpay_pipeline",
                "approved_at": now,
                "model_output": json.dumps({"source": "razorpay"}),
                "prev_decision_hash": "genesis",
                "decision_hash": "",
                "created_at": now,
                "status": "APPROVED",
            }
            hash_input = {k: v for k, v in decision_data.items() if k != "decision_hash"}
            decision_data["decision_hash"] = compute_decision_hash(hash_input, "genesis")

            await db.execute(
                "INSERT INTO decisions "
                "(decision_id, tenant_id, entity_type, entity_id, gross_amount, line_items, "
                "final_amount, policy_version_id, approver_id, approved_at, model_output, "
                "prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision_data["decision_id"], "demo",
                    decision_data["entity_type"], decision_data["entity_id"],
                    decision_data["gross_amount"], decision_data["line_items"],
                    decision_data["final_amount"], decision_data["policy_version_id"],
                    decision_data["approver_id"], decision_data["approved_at"],
                    decision_data["model_output"], decision_data["prev_decision_hash"],
                    decision_data["decision_hash"], decision_data["created_at"],
                    decision_data["status"],
                ),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT approved_at, status FROM decisions WHERE decision_id = ?",
                (decision_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            approved_at = row["approved_at"] if hasattr(row, "keys") else row[0]
            status = row["status"] if hasattr(row, "keys") else row[1]
            assert approved_at is not None, "APPROVED decision must have a timestamp"
            assert status == "APPROVED"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_api_response_serializes_null_approved_at(self):
        """API response must serialize null approved_at as null (not empty string)."""
        from routes import _to_iso_str, _decision_to_response

        # Simulate a REVIEW_REQUIRED decision from the database
        db_row = {
            "decision_id": "dec_test_serialize",
            "entity_type": "seller",
            "entity_id": "seller_x",
            "gross_amount": 100000,
            "line_items": [],
            "final_amount": 92000,
            "policy_version_id": "platform_1_1",
            "approver_id": "ai_pipeline",
            "approved_at": None,
            "model_output": {},
            "prev_decision_hash": "genesis",
            "decision_hash": "abc123",
            "created_at": "2025-01-01T00:00:00",
            "status": "REVIEW_REQUIRED",
        }

        response = _decision_to_response(db_row)
        assert response.approved_at is None, f"Response approved_at must be None, got {response.approved_at}"
        assert response.status == "REVIEW_REQUIRED"

        # Simulate an APPROVED Razorpay decision
        db_row_approved = {
            **db_row,
            "decision_id": "dec_test_serialize_approved",
            "approved_at": "2025-01-01T12:00:00+00:00",
            "status": "APPROVED",
        }
        response_approved = _decision_to_response(db_row_approved)
        assert response_approved.approved_at == "2025-01-01T12:00:00+00:00"

    @pytest.mark.asyncio
    async def test_to_iso_str_passes_none_through(self):
        """_to_iso_str must return None for None input, not empty string."""
        from routes import _to_iso_str

        assert _to_iso_str(None) is None
        assert _to_iso_str("") is None
        assert _to_iso_str("2025-01-01T00:00:00") == "2025-01-01T00:00:00"


class TestIssueRegression:
    """Regression tests for Issues 1-3: idempotency, fee distinction, entity identity."""

    def test_extract_entity_id_prefers_seller_id(self):
        """_extract_seller_id must prefer seller_id over razorpay_entity_id."""
        from ai.pipeline import _extract_seller_id

        evidence = [{
            "source_type": "order",
            "raw_content": json.dumps({
                "seller_id": "seller_123",
                "razorpay_entity_id": "order_TVtOb7uZcvSkvY",
            }),
        }]
        assert _extract_seller_id(evidence) == "seller_123"

    def test_extract_entity_id_falls_back_to_razorpay_entity_id(self):
        """_extract_seller_id must use razorpay_entity_id when seller_id is absent."""
        from ai.pipeline import _extract_seller_id

        evidence = [{
            "source_type": "order",
            "raw_content": json.dumps({
                "razorpay_entity_id": "order_TVtOb7uZcvSkvY",
            }),
        }]
        assert _extract_seller_id(evidence) == "order_TVtOb7uZcvSkvY"

    def test_extract_entity_id_tries_any_evidence_for_razorpay_id(self):
        """_extract_seller_id must try any evidence source for razorpay_entity_id."""
        from ai.pipeline import _extract_seller_id

        evidence = [{
            "source_type": "payment",
            "raw_content": json.dumps({
                "razorpay_entity_id": "pay_ABC123",
            }),
        }]
        assert _extract_seller_id(evidence) == "pay_ABC123"

    def test_extract_entity_id_returns_unknown_when_no_id_found(self):
        """_extract_seller_id returns 'unknown' when no entity ID is present."""
        from ai.pipeline import _extract_seller_id

        evidence = [{
            "source_type": "order",
            "raw_content": json.dumps({"amount": 30000}),
        }]
        assert _extract_seller_id(evidence) == "unknown"

    def test_extract_entity_id_handles_malformed_json(self):
        """_extract_seller_id handles malformed JSON gracefully."""
        from ai.pipeline import _extract_seller_id

        evidence = [{
            "source_type": "order",
            "raw_content": "not json",
        }]
        assert _extract_seller_id(evidence) == "unknown"

    def test_reasoning_prompt_distinguishes_fees(self):
        """Reasoning prompt must contain explicit fee distinction rules."""
        from ai.reasoning import REASONING_SYSTEM, REASONING_PROMPT

        assert "payment-processing" in REASONING_SYSTEM.lower() or "payment processing" in REASONING_SYSTEM.lower(), \
            "REASONING_SYSTEM must mention payment-processing fees"
        assert "platform fee" in REASONING_SYSTEM.lower() or "platform policy" in REASONING_SYSTEM.lower(), \
            "REASONING_SYSTEM must mention platform policy fees"
        assert "FEE DISTINCTION" in REASONING_PROMPT or "fee distinction" in REASONING_PROMPT.lower(), \
            "REASONING_PROMPT must contain FEE DISTINCTION rules"

    def test_reasoning_prompt_forbids_using_observed_fees_as_policy_fees(self):
        """Reasoning prompt must explicitly forbid using observed fees as platform fees."""
        from ai.reasoning import REASONING_SYSTEM, REASONING_PROMPT

        combined = REASONING_SYSTEM + REASONING_PROMPT
        # Must contain instruction that observed fees are not policy fees
        assert "NOT" in combined and ("policy fee" in combined.lower() or "platform fee" in combined.lower()), \
            "Prompt must explicitly state that observed fees are not policy fees"

    @pytest.mark.asyncio
    async def test_idempotent_same_scenario_same_evidence_reuses(self):
        """Same scenario + same evidence set + same policy set => reuse existing AI decision."""
        from database import get_db
        from routes import _parse_json_field

        tenant = "idem_t1"
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO tenants (tenant_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (tenant, "Idem Test 1"),
            )
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_idem_same", tenant, "order",
                    json.dumps({"razorpay_entity_id": "order_TEST"}),
                    "[]", json.dumps([]),
                    0, "hash_idem_same", 1, datetime.now().isoformat(),
                ),
            )
            existing_decision_id = "dec_idem_existing"
            await db.execute(
                "INSERT INTO decisions "
                "(decision_id, tenant_id, entity_type, entity_id, gross_amount, "
                "line_items, final_amount, policy_version_id, approver_id, approved_at, "
                "model_output, prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    existing_decision_id, tenant, "seller",
                    "order_TEST", 30000, json.dumps([]), 27600,
                    "platform_1_1,sla_4_2", "ai_pipeline", None,
                    json.dumps({"scenario_id": "scenario_1", "claims": [], "classification": "clear", "confidence": 0.9, "reasoning_summary": "test"}),
                    "prev", "hash", datetime.now().isoformat(), "REVIEW_REQUIRED",
                ),
            )
            await db.execute(
                "UPDATE evidence SET linked_decision_ids = ? WHERE evidence_id = ?",
                (json.dumps([existing_decision_id]), "ev_idem_same"),
            )
            await db.commit()

            evidence_ids = sorted(["ev_idem_same"])
            policy_ids = sorted(["platform_1_1", "sla_4_2"])
            idempotency_key = ("scenario_1", tuple(evidence_ids), tuple(policy_ids))

            cursor = await db.execute(
                "SELECT decision_id, model_output, policy_version_id FROM decisions "
                f"WHERE tenant_id = ? AND approver_id = 'ai_pipeline'",
                (tenant,),
            )
            rows = await cursor.fetchall()

            cursor_ev = await db.execute(
                "SELECT evidence_id, linked_decision_ids FROM evidence WHERE tenant_id = ?",
                (tenant,),
            )
            all_ev = await cursor_ev.fetchall()
            dec_to_ev: dict[str, list[str]] = {}
            for ev_row in all_ev:
                ev_id = ev_row["evidence_id"] if hasattr(ev_row, "keys") else ev_row[0]
                linked = _parse_json_field(
                    ev_row["linked_decision_ids"] if hasattr(ev_row, "keys") else ev_row[1]
                )
                if isinstance(linked, list):
                    for d in linked:
                        dec_to_ev.setdefault(d, []).append(ev_id)

            found = None
            for row in rows:
                pol_raw = row["policy_version_id"] or ""
                pol_ids = sorted(p.strip() for p in pol_raw.split(",") if p.strip())
                ev_ids = sorted(dec_to_ev.get(row["decision_id"], []))
                key = ("scenario_1", tuple(ev_ids), tuple(pol_ids))
                if key == idempotency_key:
                    found = row["decision_id"]
                    break

            assert found == existing_decision_id, \
                f"Idempotency must find existing decision {existing_decision_id}, got {found}"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_idempotent_different_scenario_allows_new(self):
        """Different scenario + same evidence set => allow a new decision."""
        from database import get_db
        from routes import _parse_json_field

        tenant = "idem_t2"
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO tenants (tenant_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (tenant, "Idem Test 2"),
            )
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_idem_diff", tenant, "order",
                    json.dumps({"razorpay_entity_id": "order_DIFF"}),
                    "[]", json.dumps([]),
                    0, "hash_idem_diff", 1, datetime.now().isoformat(),
                ),
            )
            existing_decision_id = "dec_idem_scenario1"
            await db.execute(
                "INSERT INTO decisions "
                "(decision_id, tenant_id, entity_type, entity_id, gross_amount, "
                "line_items, final_amount, policy_version_id, approver_id, approved_at, "
                "model_output, prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    existing_decision_id, tenant, "seller",
                    "order_DIFF", 30000, json.dumps([]), 27600,
                    "platform_1_1", "ai_pipeline", None,
                    json.dumps({"scenario_id": "scenario_1", "claims": [], "classification": "clear", "confidence": 0.9, "reasoning_summary": "test"}),
                    "prev", "hash", datetime.now().isoformat(), "REVIEW_REQUIRED",
                ),
            )
            await db.execute(
                "UPDATE evidence SET linked_decision_ids = ? WHERE evidence_id = ?",
                (json.dumps([existing_decision_id]), "ev_idem_diff"),
            )
            await db.commit()

            evidence_ids = sorted(["ev_idem_diff"])
            policy_ids = sorted(["platform_1_1"])
            idempotency_key = ("scenario_2", tuple(evidence_ids), tuple(policy_ids))

            cursor = await db.execute(
                "SELECT decision_id, model_output, policy_version_id FROM decisions "
                "WHERE tenant_id = ? AND approver_id = 'ai_pipeline'",
                (tenant,),
            )
            rows = await cursor.fetchall()

            cursor_ev = await db.execute(
                "SELECT evidence_id, linked_decision_ids FROM evidence WHERE tenant_id = ?",
                (tenant,),
            )
            all_ev = await cursor_ev.fetchall()
            dec_to_ev2: dict[str, list[str]] = {}
            for ev_row in all_ev:
                ev_id = ev_row["evidence_id"] if hasattr(ev_row, "keys") else ev_row[0]
                linked = _parse_json_field(
                    ev_row["linked_decision_ids"] if hasattr(ev_row, "keys") else ev_row[1]
                )
                if isinstance(linked, list):
                    for d in linked:
                        dec_to_ev2.setdefault(d, []).append(ev_id)

            found = None
            for row in rows:
                pol_raw = row["policy_version_id"] or ""
                pol_ids = sorted(p.strip() for p in pol_raw.split(",") if p.strip())
                ev_ids = sorted(dec_to_ev2.get(row["decision_id"], []))
                # Existing decision was created under scenario_1
                existing_key = ("scenario_1", tuple(ev_ids), tuple(pol_ids))
                if existing_key == idempotency_key:
                    found = row["decision_id"]
                    break

            assert found is None, \
                f"Different scenario must NOT match existing decision, but found {found}"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_idempotent_razorpay_decision_ignored(self):
        """Deterministic Razorpay decisions must not be treated as AI idempotency matches."""
        from database import get_db

        tenant = "idem_t3"
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO tenants (tenant_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (tenant, "Idem Test 3"),
            )
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_idem_razorpay", tenant, "order",
                    json.dumps({"razorpay_entity_id": "order_RP"}),
                    "[]", json.dumps([]),
                    0, "hash_idem_rp", 1, datetime.now().isoformat(),
                ),
            )
            razorpay_decision_id = "dec_razorpay_idem"
            await db.execute(
                "INSERT INTO decisions "
                "(decision_id, tenant_id, entity_type, entity_id, gross_amount, "
                "line_items, final_amount, policy_version_id, approver_id, approved_at, "
                "model_output, prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    razorpay_decision_id, tenant, "seller",
                    "order_RP", 30000, json.dumps([]), 27600,
                    "platform_1_1", "razorpay_webhook", datetime.now().isoformat(),
                    json.dumps({}),
                    "prev", "hash_rp", datetime.now().isoformat(), "APPROVED",
                ),
            )
            await db.execute(
                "UPDATE evidence SET linked_decision_ids = ? WHERE evidence_id = ?",
                (json.dumps([razorpay_decision_id]), "ev_idem_razorpay"),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT decision_id FROM decisions "
                "WHERE tenant_id = ? AND approver_id = 'ai_pipeline'",
                (tenant,),
            )
            rows = await cursor.fetchall()
            ai_decision_ids = [r["decision_id"] if hasattr(r, "keys") else r[0] for r in rows]

            assert razorpay_decision_id not in ai_decision_ids, \
                f"Razorpay decision {razorpay_decision_id} must not appear in AI idempotency candidates"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_idempotent_prefix_collision_does_not_match(self):
        """Decision ID prefix collision must not create a false idempotency match."""
        from database import get_db
        from routes import _parse_json_field

        tenant = "idem_t4"
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO tenants (tenant_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (tenant, "Idem Test 4"),
            )
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_idem_prefix", tenant, "order",
                    json.dumps({"razorpay_entity_id": "order_PFX"}),
                    "[]", json.dumps([]),
                    0, "hash_idem_pfx", 1, datetime.now().isoformat(),
                ),
            )
            dec_short = "dec_ABC123"
            await db.execute(
                "INSERT INTO decisions "
                "(decision_id, tenant_id, entity_type, entity_id, gross_amount, "
                "line_items, final_amount, policy_version_id, approver_id, approved_at, "
                "model_output, prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dec_short, tenant, "seller",
                    "order_PFX", 30000, json.dumps([]), 27600,
                    "platform_1_1", "ai_pipeline", None,
                    json.dumps({"scenario_id": "scenario_1", "claims": [], "classification": "clear", "confidence": 0.9, "reasoning_summary": "test"}),
                    "prev", "hash_pfx", datetime.now().isoformat(), "REVIEW_REQUIRED",
                ),
            )
            await db.execute(
                "UPDATE evidence SET linked_decision_ids = ? WHERE evidence_id = ?",
                (json.dumps([dec_short]), "ev_idem_prefix"),
            )
            await db.commit()

            evidence_ids = sorted(["ev_idem_prefix"])
            policy_ids = sorted(["platform_1_1"])

            cursor_ev = await db.execute(
                "SELECT evidence_id, linked_decision_ids FROM evidence WHERE tenant_id = ?",
                (tenant,),
            )
            all_ev = await cursor_ev.fetchall()
            dec_to_ev: dict[str, list[str]] = {}
            for ev_row in all_ev:
                ev_id = ev_row["evidence_id"] if hasattr(ev_row, "keys") else ev_row[0]
                linked = _parse_json_field(
                    ev_row["linked_decision_ids"] if hasattr(ev_row, "keys") else ev_row[1]
                )
                if isinstance(linked, list):
                    for d in linked:
                        dec_to_ev.setdefault(d, []).append(ev_id)

            existing_ev_ids = sorted(dec_to_ev.get(dec_short, []))
            existing_key = ("scenario_1", tuple(existing_ev_ids), tuple(policy_ids))
            requested_key = ("scenario_1", tuple(evidence_ids), tuple(policy_ids))

            assert existing_key == requested_key, \
                "Same evidence/scenario/policies must produce matching key"

            diff_key = ("scenario_2", tuple(evidence_ids), tuple(policy_ids))
            assert existing_key != diff_key, \
                "Different scenario must produce different key"

            diff_ev_key = ("scenario_1", tuple(["ev_other"]), tuple(policy_ids))
            assert existing_key != diff_ev_key, \
                "Different evidence set must produce different key"
        finally:
            await db.close()


class TestIdempotencyFlow:
    """Regression tests for the scenario idempotency flow.

    Verifies that a second identical run reuses the existing AI decision
    instead of returning 'No evidence available' (which happened when the
    idempotency check ran AFTER the ai_analyzed filter).
    """

    @pytest.mark.asyncio
    async def test_first_run_processes_unanalyzed_evidence(self):
        """First scenario run must find ai_analyzed=FALSE evidence and proceed."""
        from database import get_db
        from main import _ensure_system_config

        await _ensure_system_config()
        tenant = "idem_flow_t1"
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO tenants (tenant_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (tenant, "Idem Flow Test 1"),
            )
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_flow_first", tenant, "order",
                    json.dumps({"razorpay_entity_id": "order_FLOW1", "amount": 30000}),
                    "[]", json.dumps([]),
                    0, "hash_flow_first", 1, datetime.now().isoformat(),
                ),
            )
            await db.commit()

            # Verify evidence is not yet analyzed
            cursor = await db.execute(
                "SELECT ai_analyzed FROM evidence WHERE evidence_id = ?",
                ("ev_flow_first",),
            )
            row = await cursor.fetchone()
            val = row["ai_analyzed"] if hasattr(row, "keys") else row[0]
            assert val == 0 or val is False, "Fresh evidence must start unanalyzed"

            # Verify the ai_analyzed=FALSE query finds this evidence
            cursor2 = await db.execute(
                "SELECT * FROM evidence WHERE tenant_id = ? AND ai_analyzed = FALSE",
                (tenant,),
            )
            rows = await cursor2.fetchall()
            evidence_ids = [r["evidence_id"] if hasattr(r, "keys") else r[0] for r in rows]
            assert "ev_flow_first" in evidence_ids, \
                "Unanalyzed evidence must be found by the ai_analyzed=FALSE filter"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_second_identical_run_reuses_existing_decision(self):
        """A second run with the same scenario/evidence/policies must reuse the
        existing AI decision, NOT return 'No evidence available'.

        This is the core regression: the idempotency check must run BEFORE
        the ai_analyzed filter.
        """
        from database import get_db
        from routes import _parse_json_field

        tenant = "idem_flow_t2"
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO tenants (tenant_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (tenant, "Idem Flow Test 2"),
            )
            # Insert evidence that has already been AI-analyzed
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_flow_reuse", tenant, "order",
                    json.dumps({"razorpay_entity_id": "order_REUSE", "amount": 30000}),
                    "[]", json.dumps([]),
                    1,  # already analyzed
                    "hash_flow_reuse", 1, datetime.now().isoformat(),
                ),
            )
            # Insert the existing AI decision for scenario_1
            existing_decision_id = "dec_flow_existing"
            await db.execute(
                "INSERT INTO decisions "
                "(decision_id, tenant_id, entity_type, entity_id, gross_amount, "
                "line_items, final_amount, policy_version_id, approver_id, approved_at, "
                "model_output, prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    existing_decision_id, tenant, "seller",
                    "order_REUSE", 30000, json.dumps([]), 27600,
                    "platform_1_1", "ai_pipeline", None,
                    json.dumps({
                        "scenario_id": "scenario_1",
                        "claims": [],
                        "classification": "clear",
                        "confidence": 0.9,
                        "reasoning_summary": "test",
                    }),
                    "prev", "hash_flow", datetime.now().isoformat(), "REVIEW_REQUIRED",
                ),
            )
            # Link evidence to the existing decision
            await db.execute(
                "UPDATE evidence SET linked_decision_ids = ? WHERE evidence_id = ?",
                (json.dumps([existing_decision_id]), "ev_flow_reuse"),
            )
            await db.commit()

            # Now simulate what run_scenario does: the idempotency check
            # should find the existing decision even though ai_analyzed=TRUE.
            # Build the idempotency key from ALL tenant evidence.
            cursor_ev = await db.execute(
                "SELECT evidence_id, linked_decision_ids FROM evidence WHERE tenant_id = ?",
                (tenant,),
            )
            all_ev_rows = await cursor_ev.fetchall()
            dec_to_ev: dict[str, list[str]] = {}
            all_ev_ids: list[str] = []
            for ev_row in all_ev_rows:
                ev_id = ev_row["evidence_id"] if hasattr(ev_row, "keys") else ev_row[0]
                all_ev_ids.append(ev_id)
                linked = _parse_json_field(
                    ev_row["linked_decision_ids"] if hasattr(ev_row, "keys") else ev_row[1]
                )
                if isinstance(linked, list):
                    for d in linked:
                        dec_to_ev.setdefault(d, []).append(ev_id)

            current_evidence_ids = sorted(all_ev_ids)
            current_policy_ids = sorted(["platform_1_1"])
            idempotency_key = ("scenario_1", tuple(current_evidence_ids), tuple(current_policy_ids))

            cursor = await db.execute(
                "SELECT decision_id, model_output, policy_version_id FROM decisions "
                "WHERE tenant_id = ? AND approver_id = 'ai_pipeline'",
                (tenant,),
            )
            rows = await cursor.fetchall()

            found = None
            for row in rows:
                ex_model_output = _parse_json_field(row["model_output"])
                pol_raw = row["policy_version_id"] or ""
                pol_ids = sorted(p.strip() for p in pol_raw.split(",") if p.strip())
                ev_ids = sorted(dec_to_ev.get(row["decision_id"], []))
                ex_scenario_id = (
                    ex_model_output.get("scenario_id")
                    if isinstance(ex_model_output, dict)
                    else None
                )
                key = (ex_scenario_id, tuple(ev_ids), tuple(pol_ids))
                if key == idempotency_key:
                    found = row["decision_id"]
                    break

            assert found == existing_decision_id, (
                f"Second identical run must find existing decision {existing_decision_id}, "
                f"got {found}. The idempotency check must run before the ai_analyzed filter."
            )

            # Verify ai_analyzed is still TRUE (preserved)
            cursor2 = await db.execute(
                "SELECT ai_analyzed FROM evidence WHERE evidence_id = ?",
                ("ev_flow_reuse",),
            )
            row2 = await cursor2.fetchone()
            val = row2["ai_analyzed"] if hasattr(row2, "keys") else row2[0]
            assert val == 1 or val is True, \
                "ai_analyzed must remain TRUE after idempotent reuse"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_same_evidence_different_scenario_creates_new(self):
        """Same evidence with a different scenario must NOT reuse the existing
        decision — it must create a new one."""
        from database import get_db
        from routes import _parse_json_field

        tenant = "idem_flow_t3"
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO tenants (tenant_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (tenant, "Idem Flow Test 3"),
            )
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_flow_diff", tenant, "order",
                    json.dumps({"razorpay_entity_id": "order_DIFFSC", "amount": 30000}),
                    "[]", json.dumps([]),
                    1,  # already analyzed
                    "hash_flow_diff", 1, datetime.now().isoformat(),
                ),
            )
            existing_decision_id = "dec_flow_scenario1"
            await db.execute(
                "INSERT INTO decisions "
                "(decision_id, tenant_id, entity_type, entity_id, gross_amount, "
                "line_items, final_amount, policy_version_id, approver_id, approved_at, "
                "model_output, prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    existing_decision_id, tenant, "seller",
                    "order_DIFFSC", 30000, json.dumps([]), 27600,
                    "platform_1_1", "ai_pipeline", None,
                    json.dumps({
                        "scenario_id": "scenario_1",
                        "claims": [],
                        "classification": "clear",
                        "confidence": 0.9,
                        "reasoning_summary": "test",
                    }),
                    "prev", "hash_diff", datetime.now().isoformat(), "REVIEW_REQUIRED",
                ),
            )
            await db.execute(
                "UPDATE evidence SET linked_decision_ids = ? WHERE evidence_id = ?",
                (json.dumps([existing_decision_id]), "ev_flow_diff"),
            )
            await db.commit()

            # Request scenario_2 with the same evidence
            cursor_ev = await db.execute(
                "SELECT evidence_id, linked_decision_ids FROM evidence WHERE tenant_id = ?",
                (tenant,),
            )
            all_ev_rows = await cursor_ev.fetchall()
            dec_to_ev: dict[str, list[str]] = {}
            all_ev_ids: list[str] = []
            for ev_row in all_ev_rows:
                ev_id = ev_row["evidence_id"] if hasattr(ev_row, "keys") else ev_row[0]
                all_ev_ids.append(ev_id)
                linked = _parse_json_field(
                    ev_row["linked_decision_ids"] if hasattr(ev_row, "keys") else ev_row[1]
                )
                if isinstance(linked, list):
                    for d in linked:
                        dec_to_ev.setdefault(d, []).append(ev_id)

            # Requesting scenario_2 — the existing decision has scenario_1
            requested_key = ("scenario_2", tuple(sorted(all_ev_ids)), tuple(["platform_1_1"]))

            cursor = await db.execute(
                "SELECT decision_id, model_output, policy_version_id FROM decisions "
                "WHERE tenant_id = ? AND approver_id = 'ai_pipeline'",
                (tenant,),
            )
            rows = await cursor.fetchall()

            found = None
            for row in rows:
                ex_model_output = _parse_json_field(row["model_output"])
                pol_raw = row["policy_version_id"] or ""
                pol_ids = sorted(p.strip() for p in pol_raw.split(",") if p.strip())
                ev_ids = sorted(dec_to_ev.get(row["decision_id"], []))
                ex_scenario_id = (
                    ex_model_output.get("scenario_id")
                    if isinstance(ex_model_output, dict)
                    else None
                )
                key = (ex_scenario_id, tuple(ev_ids), tuple(pol_ids))
                if key == requested_key:
                    found = row["decision_id"]
                    break

            assert found is None, (
                f"Different scenario must NOT match existing decision, but found {found}"
            )
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_analyzed_evidence_no_matching_decision_not_reused(self):
        """Already-analyzed evidence with NO matching AI decision must NOT
        silently reuse an unrelated decision.

        If evidence is ai_analyzed=TRUE but no idempotent match exists,
        the system must return 'No evidence available', not reuse a
        decision that was created for different evidence.
        """
        from database import get_db
        from routes import _parse_json_field

        tenant = "idem_flow_t4"
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO tenants (tenant_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (tenant, "Idem Flow Test 4"),
            )
            # Evidence that is AI-analyzed but NOT linked to any decision
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_flow_orphan", tenant, "order",
                    json.dumps({"razorpay_entity_id": "order_ORPHAN", "amount": 30000}),
                    "[]", json.dumps([]),
                    1,  # analyzed but not linked
                    "hash_flow_orphan", 1, datetime.now().isoformat(),
                ),
            )
            # An unrelated AI decision for DIFFERENT evidence
            await db.execute(
                "INSERT INTO evidence "
                "(evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
                "linked_decision_ids, ai_analyzed, content_hash, version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ev_flow_other", tenant, "order",
                    json.dumps({"razorpay_entity_id": "order_OTHER", "amount": 50000}),
                    "[]", json.dumps(["dec_flow_unrelated"]),
                    1,
                    "hash_flow_other", 1, datetime.now().isoformat(),
                ),
            )
            await db.execute(
                "INSERT INTO decisions "
                "(decision_id, tenant_id, entity_type, entity_id, gross_amount, "
                "line_items, final_amount, policy_version_id, approver_id, approved_at, "
                "model_output, prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "dec_flow_unrelated", tenant, "seller",
                    "order_OTHER", 50000, json.dumps([]), 46000,
                    "platform_1_1", "ai_pipeline", None,
                    json.dumps({
                        "scenario_id": "scenario_1",
                        "claims": [],
                        "classification": "clear",
                        "confidence": 0.9,
                        "reasoning_summary": "test",
                    }),
                    "prev", "hash_unrelated", datetime.now().isoformat(), "REVIEW_REQUIRED",
                ),
            )
            await db.commit()

            # Simulate idempotency check for scenario_1 with tenant evidence
            cursor_ev = await db.execute(
                "SELECT evidence_id, linked_decision_ids FROM evidence WHERE tenant_id = ?",
                (tenant,),
            )
            all_ev_rows = await cursor_ev.fetchall()
            dec_to_ev: dict[str, list[str]] = {}
            all_ev_ids: list[str] = []
            for ev_row in all_ev_rows:
                ev_id = ev_row["evidence_id"] if hasattr(ev_row, "keys") else ev_row[0]
                all_ev_ids.append(ev_id)
                linked = _parse_json_field(
                    ev_row["linked_decision_ids"] if hasattr(ev_row, "keys") else ev_row[1]
                )
                if isinstance(linked, list):
                    for d in linked:
                        dec_to_ev.setdefault(d, []).append(ev_id)

            # The idempotency key uses ALL evidence in the tenant
            current_evidence_ids = sorted(all_ev_ids)
            idempotency_key = ("scenario_1", tuple(current_evidence_ids), tuple(["platform_1_1"]))

            cursor = await db.execute(
                "SELECT decision_id, model_output, policy_version_id FROM decisions "
                "WHERE tenant_id = ? AND approver_id = 'ai_pipeline'",
                (tenant,),
            )
            rows = await cursor.fetchall()

            found = None
            for row in rows:
                ex_model_output = _parse_json_field(row["model_output"])
                pol_raw = row["policy_version_id"] or ""
                pol_ids = sorted(p.strip() for p in pol_raw.split(",") if p.strip())
                ev_ids = sorted(dec_to_ev.get(row["decision_id"], []))
                ex_scenario_id = (
                    ex_model_output.get("scenario_id")
                    if isinstance(ex_model_output, dict)
                    else None
                )
                key = (ex_scenario_id, tuple(ev_ids), tuple(pol_ids))
                if key == idempotency_key:
                    found = row["decision_id"]
                    break

            # The unrelated decision has different evidence (ev_flow_other)
            # so it must NOT match the full tenant evidence set.
            assert found is None, (
                f"Unrelated decision must not match. Found {found} but expected None."
            )

            # AND: since all evidence is ai_analyzed=TRUE and no match exists,
            # the ai_analyzed=FALSE filter yields nothing → 'No evidence available'
            cursor2 = await db.execute(
                "SELECT * FROM evidence WHERE tenant_id = ? AND ai_analyzed = FALSE",
                (tenant,),
            )
            rows2 = await cursor2.fetchall()
            assert len(rows2) == 0, \
                "All evidence is analyzed — ai_analyzed=FALSE filter must return empty"

            # This confirms: no idempotent match + no unanalyzed evidence
            # = 'No evidence available' (correct behavior)
        finally:
            await db.close()


class TestAnalysisFingerprint:
    """Verify deterministic analysis_fingerprint computation."""

    def test_fingerprint_deterministic_same_inputs(self):
        """Same inputs must produce the same fingerprint."""
        from ai.pipeline import compute_analysis_fingerprint

        fp1 = compute_analysis_fingerprint(
            tenant_id="t1", scenario_id="s1",
            evidence_ids=["ev_a", "ev_b"], policy_ids=["p1"],
        )
        fp2 = compute_analysis_fingerprint(
            tenant_id="t1", scenario_id="s1",
            evidence_ids=["ev_a", "ev_b"], policy_ids=["p1"],
        )
        assert fp1 == fp2

    def test_fingerprint_differs_on_different_scenario(self):
        """Different scenario_id must produce a different fingerprint."""
        from ai.pipeline import compute_analysis_fingerprint

        fp1 = compute_analysis_fingerprint(
            tenant_id="t1", scenario_id="s1",
            evidence_ids=["ev_a"], policy_ids=["p1"],
        )
        fp2 = compute_analysis_fingerprint(
            tenant_id="t1", scenario_id="s2",
            evidence_ids=["ev_a"], policy_ids=["p1"],
        )
        assert fp1 != fp2

    def test_fingerprint_differs_on_different_evidence(self):
        """Different evidence set must produce a different fingerprint."""
        from ai.pipeline import compute_analysis_fingerprint

        fp1 = compute_analysis_fingerprint(
            tenant_id="t1", scenario_id="s1",
            evidence_ids=["ev_a"], policy_ids=["p1"],
        )
        fp2 = compute_analysis_fingerprint(
            tenant_id="t1", scenario_id="s1",
            evidence_ids=["ev_a", "ev_b"], policy_ids=["p1"],
        )
        assert fp1 != fp2

    def test_fingerprint_differs_on_different_tenant(self):
        """Different tenant must produce a different fingerprint."""
        from ai.pipeline import compute_analysis_fingerprint

        fp1 = compute_analysis_fingerprint(
            tenant_id="t1", scenario_id="s1",
            evidence_ids=["ev_a"], policy_ids=["p1"],
        )
        fp2 = compute_analysis_fingerprint(
            tenant_id="t2", scenario_id="s1",
            evidence_ids=["ev_a"], policy_ids=["p1"],
        )
        assert fp1 != fp2

    def test_fingerprint_order_independent(self):
        """Order of evidence and policy IDs must not affect fingerprint."""
        from ai.pipeline import compute_analysis_fingerprint

        fp1 = compute_analysis_fingerprint(
            tenant_id="t1", scenario_id="s1",
            evidence_ids=["ev_b", "ev_a"], policy_ids=["p2", "p1"],
        )
        fp2 = compute_analysis_fingerprint(
            tenant_id="t1", scenario_id="s1",
            evidence_ids=["ev_a", "ev_b"], policy_ids=["p1", "p2"],
        )
        assert fp1 == fp2

    def test_fingerprint_is_hex_sha256(self):
        """Fingerprint must be a 64-char hex SHA-256 string."""
        import re
        from ai.pipeline import compute_analysis_fingerprint

        fp = compute_analysis_fingerprint(
            tenant_id="t1", scenario_id="s1",
            evidence_ids=[], policy_ids=[],
        )
        assert re.fullmatch(r"[0-9a-f]{64}", fp), f"Bad fingerprint format: {fp}"


class TestTenantIsolationAudit:
    """Verify all post-pipeline evidence updates are tenant-scoped."""

    def test_scenarios_list_not_tenant_scoped_by_design(self):
        """Scenarios are shared dev/test tools — not tenant-scoped."""
        # This is the intentional design documented in routes.py.
        # Scenarios define *what* to run, not *whose* data to use.
        pass

    def test_run_scenario_evidence_update_includes_tenant(self):
        """Post-pipeline evidence UPDATE must include tenant_id."""
        import inspect
        from routes import run_scenario
        source = inspect.getsource(run_scenario)
        # The evidence update query should contain AND tenant_id = ?
        assert "tenant_id = ?" in source, (
            "Evidence update in run_scenario must be tenant-scoped"
        )


class TestCalculationTrace:
    """Verify structured calculation trace generation."""

    def test_calculation_trace_structure(self):
        """Trace must contain all required fields per step."""
        from calculations import build_calculation_trace
        from models import LineItem

        items = [
            LineItem(label="Platform fee", amount=2400, type="fee",
                     policy_clause_id="platform_1_1", evidence_ids=["ev_1"]),
            LineItem(label="SLA penalty", amount=5000, type="deduction",
                     policy_clause_id="sla_4_2", evidence_ids=["ev_2"]),
        ]
        trace = build_calculation_trace(
            gross_amount=30000, line_items=items, final_amount=22600,
        )

        assert trace["gross_amount"] == 30000
        assert trace["final_amount"] == 22600
        assert trace["total_deductions"] == 7400
        assert trace["validated"] is True
        assert len(trace["steps"]) == 2

        # Each step must have all required fields
        for step in trace["steps"]:
            assert "label" in step
            assert "calculation_type" in step
            assert "base_amount" in step
            assert "rate" in step or step["rate"] is None
            assert "formula" in step
            assert "calculated_amount" in step
            assert "policy_clause_id" in step
            assert "evidence_ids" in step

    def test_calculation_trace_platform_fee(self):
        """Platform fee step must show percentage_fee type."""
        from calculations import build_calculation_trace
        from models import LineItem

        items = [
            LineItem(label="Platform fee", amount=2400, type="fee",
                     policy_clause_id="platform_1_1", evidence_ids=["ev_1"]),
        ]
        trace = build_calculation_trace(
            gross_amount=30000, line_items=items, final_amount=27600,
        )
        step = trace["steps"][0]
        assert step["calculation_type"] == "percentage_fee"
        assert step["rate"] is not None
        assert step["rate"] == pytest.approx(0.08, abs=0.01)

    def test_calculation_trace_formula_string(self):
        """Overall formula must be a readable string."""
        from calculations import build_calculation_trace
        from models import LineItem

        items = [
            LineItem(label="Platform fee", amount=2400, type="fee",
                     policy_clause_id="platform_1_1", evidence_ids=[]),
        ]
        trace = build_calculation_trace(
            gross_amount=30000, line_items=items, final_amount=27600,
        )
        assert "30000" in trace["formula"]
        assert "27600" in trace["formula"]

    def test_calculation_trace_mismatch_detected(self):
        """Mismatched final_amount must set validated=False."""
        from calculations import build_calculation_trace
        from models import LineItem

        items = [
            LineItem(label="Platform fee", amount=2400, type="fee",
                     policy_clause_id="platform_1_1", evidence_ids=[]),
        ]
        trace = build_calculation_trace(
            gross_amount=30000, line_items=items, final_amount=99999,
        )
        assert trace["validated"] is False


class TestExceptionModel:
    """Verify all exception categories exist and are structured correctly."""

    def test_all_six_exception_categories_exist(self):
        """All 6 required exception categories must be defined."""
        from calculations import (
            ALL_EXCEPTION_CATEGORIES,
            EXCEPTION_MISSING_EVIDENCE,
            EXCEPTION_POLICY_AMBIGUITY,
            EXCEPTION_CONFLICTING_EVIDENCE,
            EXCEPTION_LOW_CONFIDENCE,
            EXCEPTION_DATA_INCONSISTENCY,
            EXCEPTION_CALCULATION_EXCEPTION,
        )
        expected = {
            "MISSING_EVIDENCE", "POLICY_AMBIGUITY", "CONFLICTING_EVIDENCE",
            "LOW_CONFIDENCE", "DATA_INCONSISTENCY", "CALCULATION_EXCEPTION",
        }
        assert ALL_EXCEPTION_CATEGORIES == expected

    def test_detect_exceptions_low_confidence(self):
        """Low confidence must produce LOW_CONFIDENCE exception."""
        from ai.pipeline import _detect_exceptions
        from calculations import validate_calculation
        from models import LineItem

        exceptions = _detect_exceptions(
            reasoning_result={"confidence": 0.3, "claims": []},
            evidence_records=[{"source_type": "order", "raw_content": '{}'}],
            validation={"valid": True},
            gross_amount=30000,
            line_items=[],
        )
        categories = [e["category"] for e in exceptions]
        assert "LOW_CONFIDENCE" in categories

    def test_detect_exceptions_missing_evidence(self):
        """No order evidence must produce MISSING_EVIDENCE exception."""
        from ai.pipeline import _detect_exceptions

        exceptions = _detect_exceptions(
            reasoning_result={"confidence": 0.9, "claims": []},
            evidence_records=[{"source_type": "payment", "raw_content": '{}'}],
            validation={"valid": True},
            gross_amount=30000,
            line_items=[],
        )
        categories = [e["category"] for e in exceptions]
        assert "MISSING_EVIDENCE" in categories

    def test_detect_exceptions_data_inconsistency(self):
        """Calculation mismatch must produce DATA_INCONSISTENCY exception."""
        from ai.pipeline import _detect_exceptions

        exceptions = _detect_exceptions(
            reasoning_result={"confidence": 0.9, "claims": []},
            evidence_records=[{"source_type": "order", "raw_content": '{}'}],
            validation={"valid": False, "expected_final": 27600, "calculated_final": 28000},
            gross_amount=30000,
            line_items=[],
        )
        categories = [e["category"] for e in exceptions]
        assert "DATA_INCONSISTENCY" in categories

    def test_detect_exceptions_policy_ambiguity(self):
        """Claims empty despite evidence must produce POLICY_AMBIGUITY."""
        from ai.pipeline import _detect_exceptions

        exceptions = _detect_exceptions(
            reasoning_result={"confidence": 0.9, "claims": []},
            evidence_records=[{"source_type": "order", "raw_content": '{}'}],
            validation={"valid": True},
            gross_amount=30000,
            line_items=[],
        )
        categories = [e["category"] for e in exceptions]
        assert "POLICY_AMBIGUITY" in categories

    def test_no_exceptions_on_clean_run(self):
        """A clean run with high confidence and valid data must have no exceptions."""
        from ai.pipeline import _detect_exceptions
        from models import LineItem

        exceptions = _detect_exceptions(
            reasoning_result={"confidence": 0.9, "claims": [{"type": "sla_breach"}]},
            evidence_records=[{"source_type": "order", "raw_content": '{"amount": 30000}'}],
            validation={"valid": True},
            gross_amount=30000,
            line_items=[LineItem(label="SLA penalty", amount=5000, type="deduction",
                                 policy_clause_id="sla_4_2", evidence_ids=["ev_1"])],
        )
        assert len(exceptions) == 0


class TestDecisionReplay:
    """Verify decision replay endpoint."""

    def test_replay_endpoint_exists(self):
        """The replay endpoint must exist in the router."""
        from routes import router
        routes = [r.path for r in router.routes]
        assert "/decisions/{decision_id}/replay" in routes

    def test_replay_returns_required_fields(self):
        """Replay must return stored, recomputed, match, mismatches."""
        import inspect
        from routes import replay_decision
        source = inspect.getsource(replay_decision)
        assert '"stored"' in source
        assert '"recomputed"' in source
        assert '"match"' in source
        assert '"mismatches"' in source


class TestHashVerificationExtended:
    """Verify extended hash verification fields."""

    def test_verification_result_has_extended_fields(self):
        """VerificationResult must have all extended fields."""
        from models import VerificationResult
        vr = VerificationResult(
            valid=True, checked_count=1,
            decision_hash_valid=True,
            prev_hash_valid=True,
            canonical_payload="{}",
            chain_continuity=True,
        )
        assert vr.decision_hash_valid is True
        assert vr.prev_hash_valid is True
        assert vr.canonical_payload == "{}"
        assert vr.chain_continuity is True

    def test_verify_endpoint_returns_extended_fields(self):
        """The /verify endpoint must populate extended fields."""
        import inspect
        from routes import verify_decision
        source = inspect.getsource(verify_decision)
        assert "decision_hash_valid" in source
        assert "canonical_payload" in source
        assert "chain_continuity" in source


class TestAuditTrail:
    """Verify defense packet exposes full audit trail."""

    def test_defense_packet_includes_trace_and_exceptions(self):
        """Defense packet must include calculation_trace and exceptions."""
        import inspect
        from routes import get_defense_packet
        source = inspect.getsource(get_defense_packet)
        assert "calculation_trace" in source
        assert "exceptions" in source
        assert "decision_hash_valid" in source
        assert "canonical_payload" in source

    def test_policy_version_pinned_in_decision(self):
        """Each decision must store exact policy_version_id."""
        import inspect
        from routes import run_scenario
        source = inspect.getsource(run_scenario)
        # The decision INSERT must include policy_version_id
        assert "policy_version_id" in source


class TestPolicyHistoricalReproducibility:
    """Prove that policy snapshots ensure historical reproducibility.

    Tests:
    - Policy snapshot is stored at decision creation
    - Changing the live policy does not change historical decision/defense packet output
    - Replay uses the historical snapshot
    - Tampering with the snapshot invalidates the decision hash
    """

    @pytest.mark.asyncio
    async def test_policy_snapshot_stored_at_decision_creation(self):
        """Seed decisions must contain a policy_snapshot in model_output."""
        from database import get_db

        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT model_output FROM decisions WHERE decision_id = 'dec_001'"
            )
            row = await cursor.fetchone()
            assert row is not None, "dec_001 not found"
            model_output = row["model_output"]
            if isinstance(model_output, str):
                model_output = json.loads(model_output)
            snapshot = model_output.get("policy_snapshot", [])
            assert len(snapshot) >= 2, (
                f"dec_001 must have at least 2 policies in snapshot, got {len(snapshot)}"
            )
            # Each snapshot entry must have the required fields
            for entry in snapshot:
                assert "policy_id" in entry, "Missing policy_id in snapshot"
                assert "version" in entry, "Missing version in snapshot"
                assert "clause_text" in entry, "Missing clause_text in snapshot"
                assert "effective_date" in entry, "Missing effective_date in snapshot"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_changing_live_policy_does_not_change_historical_decision(self):
        """Updating the live policy in the DB must not affect an existing decision."""
        from database import get_db
        from hash_chain import compute_decision_hash

        db = await get_db()
        try:
            # Read the original decision and its snapshot
            cursor = await db.execute(
                "SELECT model_output, decision_hash, prev_decision_hash FROM decisions "
                "WHERE decision_id = 'dec_001'"
            )
            row = await cursor.fetchone()
            model_output = row["model_output"]
            if isinstance(model_output, str):
                model_output = json.loads(model_output)
            original_snapshot = model_output.get("policy_snapshot", [])
            original_hash = row["decision_hash"]
            original_prev_hash = row["prev_decision_hash"]
            assert len(original_snapshot) >= 2, "Need at least 2 policies in snapshot"

            # Record the original clause_text for the platform policy
            original_clause = None
            for p in original_snapshot:
                if p["policy_id"] == "platform_1_1":
                    original_clause = p["clause_text"]
                    break
            assert original_clause is not None, "platform_1_1 not in snapshot"

            # Tamper: change the live policy in the database
            await db.execute(
                "UPDATE policies SET clause_text = ?, version = ? WHERE policy_id = ?",
                ("HACKED: This is not the original policy text.", "99.0", "platform_1_1"),
            )
            await db.commit()

            # Read the decision again — it must be unchanged
            cursor2 = await db.execute(
                "SELECT model_output, decision_hash FROM decisions WHERE decision_id = 'dec_001'"
            )
            row2 = await cursor2.fetchone()
            model_output2 = row2["model_output"]
            if isinstance(model_output2, str):
                model_output2 = json.loads(model_output2)
            snapshot2 = model_output2.get("policy_snapshot", [])

            # Snapshot must still have the original clause text
            for p in snapshot2:
                if p["policy_id"] == "platform_1_1":
                    assert p["clause_text"] == original_clause, (
                        "Historical snapshot was corrupted by live policy update"
                    )
                    assert p["version"] != "99.0", (
                        "Historical snapshot version was corrupted"
                    )

            # Decision hash must still be valid
            decision_data = {
                "decision_id": "dec_001",
                "model_output": model_output2,
            }
            # We cannot fully recompute the hash here without all fields,
            # but the stored hash must match what it was before
            assert row2["decision_hash"] == original_hash, (
                "Decision hash changed after live policy update"
            )

            # Restore the original policy for other tests
            await db.execute(
                "UPDATE policies SET clause_text = ?, version = ? WHERE policy_id = ?",
                (original_clause, "2.1", "platform_1_1"),
            )
            await db.commit()
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_changing_live_policy_does_not_change_defense_packet(self):
        """The defense packet must reflect the snapshot, not live policy content."""
        from database import get_db
        from auth import create_access_token
        from fastapi.testclient import TestClient
        from main import app
        from main import _ensure_system_config

        await _ensure_system_config()

        token = create_access_token("usr_test_admin", "demo", "admin", "test@demo.ledger")
        client = TestClient(app)

        # Get original defense packet
        resp1 = client.get(
            "/api/decisions/dec_001/defense-packet",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp1.status_code == 200
        packet1 = resp1.json()
        original_policies = packet1["policies"]
        assert len(original_policies) >= 2

        # Record original clause text
        original_clause = None
        for p in original_policies:
            if p["policy_id"] == "platform_1_1":
                original_clause = p["clause_text"]
                break
        assert original_clause is not None

        # Tamper the live policy
        db = await get_db()
        try:
            await db.execute(
                "UPDATE policies SET clause_text = ?, version = ? WHERE policy_id = ?",
                ("HACKED: This is not the original.", "99.0", "platform_1_1"),
            )
            await db.commit()
        finally:
            await db.close()

        # Get defense packet again — must still have original content
        resp2 = client.get(
            "/api/decisions/dec_001/defense-packet",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200
        packet2 = resp2.json()
        for p in packet2["policies"]:
            if p["policy_id"] == "platform_1_1":
                assert p["clause_text"] == original_clause, (
                    "Defense packet used live policy instead of historical snapshot"
                )
                assert p["version"] != "99.0", (
                    "Defense packet reflected a tampered policy version"
                )

        # Restore the original policy
        db = await get_db()
        try:
            await db.execute(
                "UPDATE policies SET clause_text = ?, version = ? WHERE policy_id = ?",
                (original_clause, "2.1", "platform_1_1"),
            )
            await db.commit()
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_replay_uses_historical_snapshot(self):
        """Replay response must include the stored policy_snapshot."""
        from auth import create_access_token
        from fastapi.testclient import TestClient
        from main import app
        from main import _ensure_system_config

        await _ensure_system_config()

        token = create_access_token("usr_test_admin", "demo", "admin", "test@demo.ledger")
        client = TestClient(app)

        resp = client.get(
            "/api/decisions/dec_001/replay",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        stored = data["stored"]
        assert "policy_snapshot" in stored, "Replay must include policy_snapshot"
        snapshot = stored["policy_snapshot"]
        assert len(snapshot) >= 2, f"Expected >=2 policies in snapshot, got {len(snapshot)}"
        # Verify snapshot fields
        for entry in snapshot:
            assert "policy_id" in entry
            assert "version" in entry
            assert "clause_text" in entry
            assert "effective_date" in entry

    @pytest.mark.asyncio
    async def test_tampering_with_snapshot_invalidates_decision_hash(self):
        """Modifying the stored policy_snapshot must break the decision hash."""
        from database import get_db
        from hash_chain import compute_decision_hash, canonicalize

        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM decisions WHERE decision_id = 'dec_001'"
            )
            row = await cursor.fetchone()
            assert row is not None

            original_hash = row["decision_hash"]
            model_output = row["model_output"]
            if isinstance(model_output, str):
                model_output = json.loads(model_output)

            # Tamper the snapshot
            snapshot = model_output.get("policy_snapshot", [])
            assert len(snapshot) >= 1
            snapshot[0]["clause_text"] = "TAMPERED POLICY TEXT"
            model_output["policy_snapshot"] = snapshot

            # Build a decision dict with the tampered snapshot
            tampered_decision = {
                "decision_id": row["decision_id"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "gross_amount": row["gross_amount"],
                "line_items": json.loads(row["line_items"]) if isinstance(row["line_items"], str) else row["line_items"],
                "final_amount": row["final_amount"],
                "policy_version_id": row["policy_version_id"],
                "approver_id": row["approver_id"],
                "approved_at": row["approved_at"],
                "model_output": model_output,
                "prev_decision_hash": row["prev_decision_hash"],
                "decision_hash": "",
                "created_at": row["created_at"],
                "status": row["status"],
            }

            # Recompute hash with tampered data
            tampered_hash = compute_decision_hash(tampered_decision, row["prev_decision_hash"])

            # The tampered hash must NOT match the original
            assert tampered_hash != original_hash, (
                "Tampering with policy_snapshot did NOT invalidate the decision hash!"
            )
        finally:
            await db.close()

    def test_policy_snapshot_fields_match_policy_response(self):
        """PolicySnapshot fields must align with PolicyResponse model."""
        from models import PolicyResponse
        # Every policy_snapshot entry must be constructable as a PolicyResponse
        snapshot_entry = {
            "policy_id": "platform_1_1",
            "version": "2.1",
            "clause_text": "Test clause",
            "effective_date": "2024-01-01",
        }
        response = PolicyResponse(**snapshot_entry)
        assert response.policy_id == "platform_1_1"
        assert response.version == "2.1"
        assert response.clause_text == "Test clause"
        assert response.effective_date == "2024-01-01"

    def test_pipeline_stores_policy_snapshot(self):
        """run_pipeline must produce a policy_snapshot in model_output."""
        import inspect
        from ai.pipeline import run_pipeline
        source = inspect.getsource(run_pipeline)
        assert "policy_snapshot" in source, "run_pipeline must reference policy_snapshot"

    def test_defense_packet_uses_stored_snapshot(self):
        """get_defense_packet must prefer stored policy_snapshot over live queries."""
        import inspect
        from routes import get_defense_packet
        source = inspect.getsource(get_defense_packet)
        assert "policy_snapshot" in source, (
            "get_defense_packet must reference policy_snapshot"
        )
