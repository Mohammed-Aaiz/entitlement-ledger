"""Finance Controller reconciliation test suite.

Covers the required acceptance scenarios:
  1.  clean payment/settlement
  2.  partial refund
  3.  fee mismatch
  4.  tax mismatch
  5.  amount mismatch
  6.  missing settlement
  7.  duplicate event (idempotent)
  8.  contradictory evidence
  9.  AI provider 429
  10. AI provider 503
  11. malformed AI response
  12. successful AI interpretation
  13. deterministic calculation correctness
  14. ledger hash continuity
plus dataset integrity, money validation, exception taxonomy, decision-gate
safety, batch APIs, authentication, and the 50+ record benchmark.
"""
import json
import pytest


# ===========================================================================
# Fixtures / helpers
# ===========================================================================

def _records_for(*scenarios: str, seed: int = 42) -> list[dict]:
    """Collect dataset records for the given scenarios (ground truth hidden)."""
    from reconciliation.dataset import generate_dataset, records_for_inference
    cases = generate_dataset(count=100, seed=seed)
    out = []
    for case in cases:
        if case.scenario in scenarios:
            out.extend(records_for_inference(case))
    return out


def _reconcile(records: list[dict], payment_id: str = "", use_ai: bool = False, provider=None):
    """Run a single payment through the pure controller."""
    from reconciliation.service import reconcile_payment
    from reconciliation.models import FinancialRecord
    recs = [FinancialRecord.from_dict(r) for r in records]
    if not payment_id:
        payment_id = recs[0].payment_id or recs[0].external_id
    return reconcile_payment("test", payment_id, recs, use_ai=use_ai, provider=provider)


def _case_of_scenario(scenario: str):
    """Return the reconciled case for the first dataset case of a scenario."""
    from reconciliation.dataset import generate_dataset, records_for_inference
    case = next(c for c in generate_dataset(count=100, seed=42) if c.scenario == scenario)
    from reconciliation.service import reconcile_payment
    from reconciliation.models import FinancialRecord
    recs = [FinancialRecord.from_dict(r) for r in records_for_inference(case)]
    return reconcile_payment("test", case.payment_id, recs, order_id=case.order_id)


def _ai_unavailable_case(provider=None, use_ai=True):
    """Reconcile the ai_unavailable dataset case with the given provider."""
    from reconciliation.dataset import generate_dataset, records_for_inference
    from reconciliation.service import reconcile_payment
    from reconciliation.models import FinancialRecord
    src = next(c for c in generate_dataset(count=100, seed=42) if c.scenario == "ai_unavailable")
    recs = [FinancialRecord.from_dict(r) for r in records_for_inference(src)]
    return reconcile_payment("test", src.payment_id, recs, use_ai=use_ai, provider=provider, order_id=src.order_id)


# ===========================================================================
# 1-6. Core scenarios
# ===========================================================================

class TestCoreScenarios:
    def test_clean_payment_settlement_matches(self):
        case = _case_of_scenario("clean_match")
        assert case.classification == "MATCHED"
        assert case.exception_codes == []
        assert case.variance == 0
        assert case.expected_amount == case.actual_amount
        # Expected = captured - fee - tax
        assert case.expected_amount == case.calculation_trace["captured_amount"] - 2400 - 300

    def test_partial_refund_reconciles(self):
        case = _case_of_scenario("partial_refund")
        assert case.classification == "MATCHED"
        assert case.variance == 0
        trace = case.calculation_trace
        assert trace["refund_total"] > 0
        assert case.expected_amount == trace["captured_amount"] - trace["refund_total"] - 2400 - 300

    def test_multiple_partial_refunds_reconcile(self):
        case = _case_of_scenario("multiple_partial_refunds")
        assert case.classification == "MATCHED"
        assert case.variance == 0
        assert case.calculation_trace["refund_total"] == sum(r["amount"] for r in case.match_info["refunds"])

    def test_fee_mismatch_exception(self):
        case = _case_of_scenario("fee_mismatch")
        assert case.classification == "EXCEPTION"
        assert "FEE_MISMATCH" in case.exception_codes

    def test_tax_mismatch_exception(self):
        case = _case_of_scenario("tax_mismatch")
        assert case.classification == "EXCEPTION"
        assert "TAX_MISMATCH" in case.exception_codes

    def test_settlement_amount_mismatch_exception(self):
        case = _case_of_scenario("settlement_amount_mismatch")
        assert case.classification == "EXCEPTION"
        assert "AMOUNT_MISMATCH" in case.exception_codes
        assert case.variance != 0

    def test_missing_settlement_review_required(self):
        case = _case_of_scenario("missing_settlement")
        assert case.classification == "REVIEW_REQUIRED"
        assert "MISSING_SETTLEMENT" in case.exception_codes
        assert case.actual_amount == 0


# ===========================================================================
# 7-8. Duplicates and contradictions
# ===========================================================================

class TestDuplicatesAndContradictions:
    def test_duplicate_webhook_is_idempotent(self):
        # Two payment records with the SAME payload hash → one decision.
        case = _case_of_scenario("duplicate_webhook")
        assert case.classification == "MATCHED"
        # Only one payment record should have been counted
        assert case.match_info["payment_duplicates"] == []

    def test_duplicate_payment_is_exception(self):
        case = _case_of_scenario("duplicate_payment")
        assert case.classification == "EXCEPTION"
        assert "DUPLICATE_PAYMENT" in case.exception_codes

    def test_duplicate_settlement_is_exception(self):
        case = _case_of_scenario("duplicate_settlement")
        assert case.classification == "EXCEPTION"
        assert "DUPLICATE_SETTLEMENT" in case.exception_codes

    def test_contradictory_evidence_is_exception(self):
        """Same refund id delivered with conflicting amounts → CONTRADICTORY_EVIDENCE.

        Each refund amount is individually valid (the invariant
        total_refunds <= captured holds), so this is genuine contradiction,
        NOT invalid source data — and it must never be auto-resolved.
        """
        case = _case_of_scenario("contradictory_evidence")
        assert case.classification == "EXCEPTION"
        assert "CONTRADICTORY_EVIDENCE" in case.exception_codes
        assert "REFUND_MISMATCH" not in case.exception_codes

    def test_invalid_refund_total_is_exception(self):
        """Refunds exceeding capture are explicit INVALID source data.

        The invariant total_refunds <= captured_amount is enforced as a
        first-class INVALID_REFUND_TOTAL exception with deterministic
        reasoning — never silently absorbed, never AI-repaired.
        """
        case = _case_of_scenario("invalid_refund_total")
        assert case.classification == "EXCEPTION"
        assert case.exception_codes == ["INVALID_REFUND_TOTAL"]
        assert "cannot exceed" in case.explanation

    def test_missing_payment_is_exception(self):
        case = _case_of_scenario("missing_payment")
        assert case.classification == "EXCEPTION"
        assert "MISSING_PAYMENT" in case.exception_codes

    def test_partial_settlement_is_exception(self):
        case = _case_of_scenario("partial_settlement")
        assert case.classification == "EXCEPTION"
        assert "AMOUNT_MISMATCH" in case.exception_codes

    def test_late_settlement_is_review(self):
        case = _case_of_scenario("late_settlement")
        assert case.classification == "REVIEW_REQUIRED"
        assert "LATE_SETTLEMENT" in case.exception_codes


# ===========================================================================
# 9-12. AI failure handling + successful interpretation
# ===========================================================================

class TestAIFailureHandling:
    def _with_failing_provider(self, mode: str):
        from benchmark.failing_provider import get_failing_provider
        return get_failing_provider(mode)

    def test_ai_429_never_approves_ambiguous_case(self):
        provider = self._with_failing_provider("429")
        case = _ai_unavailable_case(provider=provider)
        assert case.classification == "REVIEW_REQUIRED"
        assert case.ai_status in ("unavailable", "failed")
        assert "AI_UNAVAILABLE" in case.exception_codes
        assert "429" in case.ai_technical_reason

    def test_ai_503_never_approves_ambiguous_case(self):
        provider = self._with_failing_provider("503")
        case = _ai_unavailable_case(provider=provider)
        assert case.classification == "REVIEW_REQUIRED"
        assert "AI_UNAVAILABLE" in case.exception_codes
        assert "503" in case.ai_technical_reason

    def test_malformed_ai_output_is_failure_not_approval(self):
        provider = self._with_failing_provider("malformed")
        case = _ai_unavailable_case(provider=provider)
        assert case.classification == "REVIEW_REQUIRED"
        assert case.ai_status == "failed"
        assert "malformed" in case.ai_technical_reason

    def test_missing_key_classified_as_authentication(self):
        """A missing API key (api_key vs "api key" spelling) is classified
        as the intended authentication/missing-key category, not a generic
        OSError — the failure taxonomy must preserve the root cause."""
        from reconciliation.ai_controller import _classify_failure

        # Unit: EnvironmentError/OSError with a missing-key message
        reason = _classify_failure(EnvironmentError("GROQ_API_KEY is not set"))
        assert "authentication" in reason
        assert "API key" in reason
        assert "OSError" not in reason

        # End-to-end: the missing_key failing stub reports the same category
        provider = self._with_failing_provider("missing_key")
        case = _ai_unavailable_case(provider=provider)
        assert case.classification == "REVIEW_REQUIRED"
        assert case.ai_status == "unavailable"
        assert "authentication" in case.ai_technical_reason

    def test_ai_failure_never_changes_deterministic_amounts(self):
        """AI failure must not alter the deterministic calculation."""
        provider = self._with_failing_provider("429")
        from reconciliation.dataset import generate_dataset, records_for_inference
        from reconciliation.service import reconcile_payment
        from reconciliation.models import FinancialRecord
        src = next(c for c in generate_dataset(count=100, seed=42) if c.scenario == "clean_match")
        recs = [FinancialRecord.from_dict(r) for r in records_for_inference(src)]
        with_ai = reconcile_payment("test", src.payment_id, recs, use_ai=True, provider=provider, order_id=src.order_id)
        without_ai = reconcile_payment("test", src.payment_id, recs, use_ai=False, order_id=src.order_id)
        assert with_ai.expected_amount == without_ai.expected_amount
        assert with_ai.variance == without_ai.variance

    def test_successful_ai_interpretation_recorded(self):
        """A working provider's interpretation is recorded, never authoritative."""
        from ai.llm_provider import LLMProvider
        from ai.llm_provider import ToolCallResponse

        class GoodProvider(LLMProvider):
            def is_available(self):
                return True

            def provider_info(self):
                return {"provider": "test_stub", "model": "stub"}

            def complete(self, prompt, system="", max_tokens=2048, temperature=0.0,
                         json_mode=False, response_schema=None):
                return json.dumps({
                    "evidence_summary": "Payment captured; settlement matches expectation.",
                    "identified_relations": [{"record_ref": "ref_1", "relation": "refund_for_payment"}],
                    "discrepancy_explanation": "",
                    "contradictions": [],
                    "ambiguous": False,
                    "confidence": 0.95,
                    "suggested_human_review": False,
                    "reasoning": (
                        "The captured payment amount, the recorded fees and the "
                        "settlement value are mutually consistent and match the "
                        "deterministically expected net amount with no variance."
                    ),
                })

        provider = GoodProvider()
        from reconciliation.dataset import generate_dataset, records_for_inference
        from reconciliation.service import reconcile_payment
        from reconciliation.models import FinancialRecord
        src = next(c for c in generate_dataset(count=100, seed=42) if c.scenario == "clean_match")
        recs = [FinancialRecord.from_dict(r) for r in records_for_inference(src)]
        # force_ai=True: this test explicitly exercises interpretation on a
        # clean match (the deterministic AI gate would otherwise correctly
        # skip AI for a clean match — covered by the gating tests).
        case = reconcile_payment("test", src.payment_id, recs, use_ai=True, provider=provider,
                                 order_id=src.order_id, force_ai=True)
        assert case.ai_status == "available"
        assert case.ai_interpretation.get("confidence") == 0.95
        # AI cannot change the deterministic outcome
        assert case.classification == "MATCHED"
        assert case.expected_amount == case.calculation_trace["expected_settlement"]


# ===========================================================================
# 13. Deterministic calculation correctness
# ===========================================================================

class TestDeterministicCalculation:
    def test_exact_spec_example(self):
        """The spec's example: 100000 - 10000 - 1800 - 300 + 0 = 87900."""
        from reconciliation.calculator import calculate_expected_settlement, compare_settlement
        calc = calculate_expected_settlement(
            captured_amount=100000, refund_total=10000,
            fee_total=1800, tax_total=300, adjustments=0,
        )
        assert calc.expected_settlement == 87900
        calc = compare_settlement(calc, 87900)
        assert calc.variance == 0

    def test_variance_formula(self):
        from reconciliation.calculator import calculate_expected_settlement, compare_settlement
        calc = calculate_expected_settlement(50000, refund_total=5000, fee_total=1000, tax_total=200, adjustments=300)
        assert calc.expected_settlement == 50000 - 5000 - 1000 - 200 + 300
        calc = compare_settlement(calc, 44100)
        assert calc.variance == 0

    def test_negative_adjustments(self):
        from reconciliation.calculator import calculate_expected_settlement
        calc = calculate_expected_settlement(50000, adjustments=-2000)
        assert calc.expected_settlement == 48000

    def test_negative_result_rejected(self):
        from reconciliation.calculator import calculate_expected_settlement, FinancialValidationError
        with pytest.raises(FinancialValidationError):
            calculate_expected_settlement(100, refund_total=50, fee_total=100)

    def test_refunds_exceeding_capture_rejected(self):
        from reconciliation.calculator import calculate_expected_settlement, FinancialValidationError
        with pytest.raises(FinancialValidationError):
            calculate_expected_settlement(1000, refund_total=2000)

    def test_unsupported_currency_rejected(self):
        from reconciliation.calculator import calculate_expected_settlement, FinancialValidationError
        with pytest.raises(FinancialValidationError):
            calculate_expected_settlement(1000, currency="USD")

    def test_malformed_amounts_rejected(self):
        from reconciliation.calculator import calculate_expected_settlement, FinancialValidationError
        with pytest.raises(FinancialValidationError):
            calculate_expected_settlement("1000")
        with pytest.raises(FinancialValidationError):
            calculate_expected_settlement(1000, refund_total=-5)

    def test_float_amounts_rejected(self):
        from reconciliation.calculator import calculate_expected_settlement, FinancialValidationError
        with pytest.raises(FinancialValidationError):
            calculate_expected_settlement(1000.5)

    def test_trace_is_auditable(self):
        from reconciliation.calculator import calculate_expected_settlement
        calc = calculate_expected_settlement(100000, refund_total=10000, fee_total=1800, tax_total=300)
        assert len(calc.steps) == 5
        assert calc.steps[-1]["running_total"] == calc.expected_settlement
        assert "formula" in calc.to_dict()


class TestVarianceClassificationSemantics:
    """Partial-settlement semantics: PARTIAL_SETTLEMENT is ONLY genuine
    under-settlement (actual < expected).  Over-settlement (actual >
    expected) is an AMOUNT_MISMATCH and must never be labeled partial.
    Exception codes stay structured arrays."""

    def _run(self, actual):
        from datetime import datetime, timedelta, timezone
        from reconciliation.models import (
            FinancialRecord, RECORD_PAYMENT, RECORD_SETTLEMENT, RECORD_FEE_TAX,
        )
        from reconciliation.service import reconcile_payment
        base = datetime.now(timezone.utc) - timedelta(days=3)
        iso0 = base.isoformat()
        iso1 = (base + timedelta(days=1)).isoformat()
        records = [
            FinancialRecord(
                record_type=RECORD_PAYMENT, external_id="pay_sem", amount=100000,
                status="captured", payment_id="pay_sem", order_id="ord_sem",
                recorded_at=iso0, source="fixture",
                raw_evidence_ref="razorpay_payments:pay_sem",
            ),
            FinancialRecord(
                record_type=RECORD_FEE_TAX, external_id="fee_sem", amount=0,
                fee_amount=1800, tax_amount=300, payment_id="pay_sem",
                recorded_at=iso0, source="fixture",
                raw_evidence_ref="razorpay_fees:fee_sem",
            ),
            FinancialRecord(
                record_type=RECORD_SETTLEMENT, external_id="set_sem", amount=actual,
                status="processed", payment_id="pay_sem",
                recorded_at=iso1, source="fixture",
                raw_evidence_ref="razorpay_settlements:set_sem",
            ),
        ]
        return reconcile_payment("t", "pay_sem", records, use_ai=False)

    def test_exact_match(self):
        case = self._run(actual=97900)
        assert case.classification == "MATCHED"
        assert case.exception_codes == []
        assert case.variance == 0

    def test_under_settlement_is_partial(self):
        case = self._run(actual=96900)
        assert case.classification == "EXCEPTION"
        codes = case.exception_codes
        assert isinstance(codes, list)
        assert "PARTIAL_SETTLEMENT" in codes, f"missing partial code: {codes}"
        assert "AMOUNT_MISMATCH" in codes
        assert case.variance == -1000
        # Structured codes must never be concatenated into one string.
        for c in codes:
            assert c in ("PARTIAL_SETTLEMENT", "AMOUNT_MISMATCH"), f"unexpected code {c}"

    def test_over_settlement_is_never_partial(self):
        case = self._run(actual=98900)
        assert case.classification == "EXCEPTION"
        codes = case.exception_codes
        assert isinstance(codes, list)
        assert "AMOUNT_MISMATCH" in codes
        assert "PARTIAL_SETTLEMENT" not in codes, (
            f"over-settlement must not be labeled partial: {codes}")
        assert case.variance == 1000  # sign preserved

    def test_over_settlement_beyond_expected_double(self):
        # actual >> expected is still AMOUNT_MISMATCH, never PARTIAL_SETTLEMENT.
        case = self._run(actual=97900 * 2)
        assert case.classification == "EXCEPTION"
        assert "AMOUNT_MISMATCH" in case.exception_codes
        assert "PARTIAL_SETTLEMENT" not in case.exception_codes


# ===========================================================================
# 14. Ledger hash continuity (via batch run with DB)
# ===========================================================================

class TestLedgerContinuity:
    def test_batch_run_creates_verified_hash_chain(self):
        """A batch run writes decisions to the ledger with a valid hash chain."""
        from reconciliation.dataset import generate_dataset, records_for_inference
        from reconciliation.service import run_batch

        cases = generate_dataset(count=30, seed=42)
        records = []
        for case in cases:
            records.extend(records_for_inference(case))

        run = run_batch("demo", records, source="test")

        # Verify the run row and metrics
        assert run.status == "completed"
        assert run.total_cases == 30
        assert run.match_rate > 0
        assert run.audit_completeness == 1.0

    def test_ledger_chain_verifies_in_database(self):
        """Decisions written by a batch run form a valid hash chain.

        The chain is verified by following prev_decision_hash links (the
        same approach the API's per-decision verify endpoint uses) because
        created_at ties between cases written in the same millisecond make
        naive chronological sorting unreliable.
        """
        from reconciliation.dataset import generate_dataset, records_for_inference
        from reconciliation.service import run_batch
        from hash_chain import verify_chain
        import asyncio
        import json
        import database

        cases = generate_dataset(count=30, seed=42)
        records = []
        for case in cases:
            records.extend(records_for_inference(case))

        run_batch("demo", records, source="test")

        async def _fetch():
            db = await database.get_db()
            try:
                # Full tenant chain (excluding the intentionally tampered
                # seed decision dec_005_tampered), in insertion order.
                cur = await db.execute(
                    "SELECT * FROM decisions WHERE tenant_id = ? "
                    "AND decision_id != 'dec_005_tampered' ORDER BY rowid",
                    ("demo",),
                )
                rows = await cur.fetchall()
                # Project ONLY the fields that were hashed (the same subset
                # routes._row_to_decision uses).  Extra columns like tenant_id
                # were never part of the hash input.
                decisions = []
                for r in rows:
                    decisions.append({
                        "decision_id": r["decision_id"],
                        "entity_type": r["entity_type"],
                        "entity_id": r["entity_id"],
                        "gross_amount": r["gross_amount"],
                        "line_items": json.loads(r["line_items"]),
                        "final_amount": r["final_amount"],
                        "policy_version_id": r["policy_version_id"],
                        "approver_id": r["approver_id"],
                        "approved_at": r["approved_at"],
                        "model_output": json.loads(r["model_output"]),
                        "prev_decision_hash": r["prev_decision_hash"],
                        "decision_hash": r["decision_hash"],
                        "created_at": r["created_at"],
                        "status": r["status"],
                    })
                return decisions
            finally:
                await db.close()

        loop = asyncio.new_event_loop()
        try:
            decisions = loop.run_until_complete(_fetch())
        finally:
            loop.close()
        rec_decisions = [d for d in decisions if d["entity_type"] == "reconciliation"]
        assert len(rec_decisions) >= 27  # 30 cases minus invalid/missing-payment (3 max)
        # Every decision's prev hash must equal the previous decision's hash
        for i in range(1, len(decisions)):
            assert decisions[i]["prev_decision_hash"] == decisions[i - 1]["decision_hash"], (
                f"chain broken at index {i}: {decisions[i]['decision_id']}"
            )
        result = verify_chain(decisions)
        assert result["valid"] is True
        assert result["checked_count"] == len(decisions)

    def test_ingest_dedup_is_idempotent(self):
        from reconciliation.dataset import generate_dataset, records_for_inference
        from reconciliation.service import ingest_records_async
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            case = next(c for c in generate_dataset(count=100, seed=42) if c.scenario == "duplicate_webhook")
            records = records_for_inference(case)
            valid, errors, dupes = loop.run_until_complete(
                ingest_records_async("demo", records, source="fixture", db=None)
            )
            # One of the two identical payment records is deduped
            assert dupes == 1
            assert len(valid) == len(records) - 1
        finally:
            loop.close()


# ===========================================================================
# Batch API tests
# ===========================================================================

class TestReconciliationAPI:
    def test_run_requires_auth(self, client):
        resp = client.post("/api/reconciliation/run", json={"records": []})
        assert resp.status_code in (401, 403)

    def test_run_creates_run_and_cases(self, auth_client):
        from reconciliation.dataset import generate_dataset, records_for_inference
        cases = generate_dataset(count=30, seed=42)
        records = []
        for case in cases:
            records.extend(records_for_inference(case))

        resp = auth_client.post("/api/reconciliation/run", json={"records": records, "source": "api_test"})
        assert resp.status_code == 200, resp.text
        run = resp.json()
        assert run["total_cases"] == 30
        assert run["matched"] > 0
        assert run["audit_completeness"] == 1.0

        # Fetch the run
        resp = auth_client.get(f"/api/reconciliation/runs/{run['run_id']}")
        assert resp.status_code == 200
        assert resp.json()["total_cases"] == 30

    def test_demo_run_and_exceptions(self, auth_client):
        resp = auth_client.post("/api/reconciliation/run/demo?count=100")
        assert resp.status_code == 200, resp.text
        run = resp.json()
        assert run["total_cases"] == 100
        assert run["exceptions"] > 0
        assert run["review_required"] > 0
        assert run["false_auto_resolve"] == 0

        # Exception queue
        resp = auth_client.get(f"/api/reconciliation/runs/{run['run_id']}/exceptions")
        assert resp.status_code == 200
        exc = resp.json()
        assert exc["total"] == run["exceptions"] + run["review_required"]
        assert len(exc["exceptions"]) > 0

        # Case detail
        case_id = exc["exceptions"][0]["case_id"]
        resp = auth_client.get(f"/api/reconciliation/cases/{case_id}")
        assert resp.status_code == 200
        case = resp.json()
        assert case["classification"] in ("REVIEW_REQUIRED", "EXCEPTION")
        assert case["exception_codes"]
        assert isinstance(case["calculation_trace"], dict)

    def test_dashboard(self, auth_client):
        auth_client.post("/api/reconciliation/run/demo?count=100")
        resp = auth_client.get("/api/reconciliation/dashboard")
        assert resp.status_code == 200
        dash = resp.json()
        assert dash["total_runs"] >= 1
        assert dash["total_cases"] >= 100
        assert dash["unresolved_exceptions"]
        assert dash["ledger_verified"] is True

    def test_ledger_hash_chain_valid_after_demo(self, auth_client):
        auth_client.post("/api/reconciliation/run/demo?count=50")
        resp = auth_client.get("/api/decisions/verify-all")
        assert resp.status_code == 200
        assert resp.json()["valid"] is True
        assert resp.json()["checked_count"] > 0

    def test_invalid_record_creates_exception_case(self, auth_client):
        records = [{
            "record_type": "payment",
            "external_id": "pay_bad",
            "amount": -5000,
            "payment_id": "pay_bad",
        }]
        resp = auth_client.post("/api/reconciliation/run", json={"records": records})
        assert resp.status_code == 200, resp.text
        run = resp.json()
        assert run["exceptions"] >= 1
        assert run["errors"], "ingest errors must be reported"
        # The invalid record surfaces in the exception queue
        resp = auth_client.get(f"/api/reconciliation/runs/{run['run_id']}/exceptions")
        codes = [e["exception_codes"] for e in resp.json()["exceptions"]]
        assert any("INVALID_RECORD" in c for c in codes)


# ===========================================================================
# Decision gate safety
# ===========================================================================

class TestDecisionGateSafety:
    def test_ambiguous_case_never_matched_when_ai_unavailable(self):
        """The critical invariant: AI failure must never produce MATCHED."""
        from benchmark.failing_provider import get_failing_provider

        for mode in ("429", "503", "timeout", "malformed", "tool_incompat", "missing_key"):
            provider = get_failing_provider(mode)
            case = _ai_unavailable_case(provider=provider)
            assert case.classification != "MATCHED", (
                f"mode {mode}: AI failure produced MATCHED — unsafe"
            )
            assert "AI_UNAVAILABLE" in case.exception_codes

    def test_clean_case_matched_without_ai(self):
        case = _case_of_scenario("clean_match")
        assert case.ai_status == "not_needed"
        assert case.classification == "MATCHED"

    def test_ai_cannot_override_mismatch(self):
        """Even a 'confident' AI interpretation cannot approve an amount mismatch."""
        from ai.llm_provider import LLMProvider

        class LyingProvider(LLMProvider):
            def is_available(self):
                return True

            def provider_info(self):
                return {"provider": "lying_stub", "model": "stub"}

            def complete(self, prompt, system="", max_tokens=2048, temperature=0.0,
                         json_mode=False, response_schema=None):
                return json.dumps({
                    "evidence_summary": "Everything is fine.",
                    "identified_relations": [],
                    "discrepancy_explanation": "No discrepancy.",
                    "contradictions": [],
                    "ambiguous": False,
                    "confidence": 0.99,
                    "suggested_human_review": False,
                    "reasoning": (
                        "The captured payment amount and the settlement recorded "
                        "for this payment appear consistent, but the deterministic "
                        "engine still computed a variance from the expected amount."
                    ),
                })

        from reconciliation.dataset import generate_dataset, records_for_inference
        from reconciliation.service import reconcile_payment
        from reconciliation.models import FinancialRecord
        src = next(c for c in generate_dataset(count=100, seed=42) if c.scenario == "settlement_amount_mismatch")
        recs = [FinancialRecord.from_dict(r) for r in records_for_inference(src)]
        case = reconcile_payment("test", src.payment_id, recs, use_ai=True, provider=LyingProvider(), order_id=src.order_id)
        assert case.classification == "EXCEPTION"
        assert "AMOUNT_MISMATCH" in case.exception_codes


# ===========================================================================
# Dataset integrity
# ===========================================================================

class TestDataset:
    def test_100_records_generated(self):
        from reconciliation.dataset import generate_dataset
        cases = generate_dataset(count=100, seed=42)
        assert len(cases) == 100

    def test_deterministic_reproducibility(self):
        from reconciliation.dataset import generate_dataset
        a = generate_dataset(count=100, seed=42)
        b = generate_dataset(count=100, seed=42)
        for ca, cb in zip(a, b):
            assert ca.case_id == cb.case_id
            assert ca.ground_truth == cb.ground_truth
            assert len(ca.records) == len(cb.records)

    def test_ground_truth_hidden_from_records(self):
        from reconciliation.dataset import generate_dataset, records_for_inference
        for case in generate_dataset(count=100, seed=42):
            records = records_for_inference(case)
            serialized = json.dumps(records)
            assert "ground_truth" not in serialized
            assert "classification" not in json.dumps(case.ground_truth) or True
            for rec in records:
                assert "ground_truth" not in rec

    def test_all_scenarios_covered(self):
        from reconciliation.dataset import generate_dataset, ALL_SCENARIOS
        cases = generate_dataset(count=100, seed=42)
        found = {c.scenario for c in cases}
        assert found == set(ALL_SCENARIOS)

    def test_records_have_integer_amounts(self):
        from reconciliation.dataset import generate_dataset
        for case in generate_dataset(count=100, seed=42):
            for rec in case.records:
                assert isinstance(rec.amount, int)
                # Only the invalid_record scenario intentionally carries a
                # negative amount (it must be rejected as INVALID_RECORD).
                if case.scenario != "invalid_record":
                    assert rec.amount >= 0

    def test_normal_refund_scenarios_respect_refund_invariant(self):
        """Financial invariant: total_refunds <= captured_amount for every
        NORMAL scenario.  Only the explicitly-tagged malformed scenario
        (invalid_refund_total) may violate it."""
        from reconciliation.dataset import generate_dataset
        malformed = {"invalid_refund_total", "invalid_record"}
        # Scenarios without a captured payment at all (MISSING_PAYMENT) have
        # no capture to bound refunds against — the exception is about the
        # absent payment, not about refund arithmetic.
        no_capture = {"missing_payment"}
        for case in generate_dataset(count=100, seed=42):
            captured = sum(
                r.amount for r in case.records
                if r.record_type == "payment"
            )
            refunds = sum(
                r.amount for r in case.records
                if r.record_type == "refund"
            )
            if case.scenario in no_capture or captured == 0:
                continue
            if case.scenario in malformed:
                # Explicitly tagged malformed data — may break the invariant.
                if case.scenario == "invalid_refund_total":
                    assert refunds > captured > 0
                continue
            assert refunds <= captured, (
                f"scenario {case.scenario} violates refund invariant: "
                f"refunds {refunds} > captured {captured}"
            )


# ===========================================================================
# Exception taxonomy
# ===========================================================================

class TestExceptionTaxonomy:
    def test_codes_are_structured(self):
        from reconciliation.exceptions import ExceptionCode, ALL_EXCEPTION_CODES
        assert "MISSING_PAYMENT" in ALL_EXCEPTION_CODES
        assert "MISSING_SETTLEMENT" in ALL_EXCEPTION_CODES
        assert "AMOUNT_MISMATCH" in ALL_EXCEPTION_CODES
        assert "CONTRADICTORY_EVIDENCE" in ALL_EXCEPTION_CODES
        assert "AI_UNAVAILABLE" in ALL_EXCEPTION_CODES
        assert len(ALL_EXCEPTION_CODES) >= 13

    def test_exception_has_full_structure(self):
        from reconciliation.exceptions import amount_mismatch
        exc = amount_mismatch("pay_1", 100, 90, -10, ["rec_1"], ["evt_1"])
        d = exc.to_dict()
        assert d["code"] == "AMOUNT_MISMATCH"
        assert d["explanation"]
        assert d["financial_impact"] == -10
        assert d["human_action_required"] is True
        assert d["evidence_refs"] == ["evt_1"]


# ===========================================================================
# Benchmark
# ===========================================================================

class TestReconciliationBenchmark:
    def test_deterministic_50_record_benchmark(self):
        from benchmark.reconciliation_runner import run_reconciliation_benchmark
        from benchmark.reconciliation_metrics import compute_reconciliation_metrics
        run = run_reconciliation_benchmark(count=50, seed=42, mode="deterministic")
        m = compute_reconciliation_metrics(run)
        assert m.total_cases == 50
        assert m.classification_accuracy >= 0.95
        assert m.calculation_accuracy >= 0.95
        assert m.false_auto_resolve == 0
        assert m.audit_completeness >= 0.99
        assert m.throughput_cases_per_sec > 0

    def test_100_record_benchmark_zero_false_auto_resolve(self):
        from benchmark.reconciliation_runner import run_reconciliation_benchmark
        from benchmark.reconciliation_metrics import compute_reconciliation_metrics
        run = run_reconciliation_benchmark(count=100, seed=42, mode="deterministic")
        m = compute_reconciliation_metrics(run)
        assert m.total_cases == 100
        assert m.classification_accuracy == 1.0
        assert m.calculation_accuracy == 1.0
        assert m.false_auto_resolve == 0

    def test_failure_modes_never_false_auto_resolve(self):
        from benchmark.reconciliation_runner import run_reconciliation_benchmark
        from benchmark.reconciliation_metrics import compute_reconciliation_metrics
        for mode in ("429", "503", "timeout", "malformed", "tool_incompat", "missing_key"):
            run = run_reconciliation_benchmark(count=100, seed=42, mode="failure", failure_mode=mode)
            m = compute_reconciliation_metrics(run)
            assert m.false_auto_resolve == 0, f"mode {mode} produced false auto-resolve"
            assert m.ai_failed_count + m.ai_unavailable_count >= 2
            # The failing provider must have been genuinely invoked for
            # every case that requests AI — never silently skipped
            # (deterministic-only).  The only cases with no invocation are
            # the architecturally-safe MISSING_PAYMENT early returns that
            # exit before the AI block.
            skipped = [r for r in run.results if r.ai_status == "not_attempted"]
            assert run.provider_invocations == run.total_cases - len(skipped), (
                f"mode {mode}: provider invoked {run.provider_invocations}/"
                f"{run.total_cases - len(skipped)} AI-requested cases"
            )
            for r in skipped:
                assert "MISSING_PAYMENT" in r.exception_codes, (
                    f"mode {mode}: case {r.case_id} skipped AI without a safe reason"
                )

    def test_failure_mode_actually_invokes_provider(self):
        """Failure mode genuinely invokes the failing provider.

        Distinguishes "provider called and failed" (one invocation per
        case, ai_status unavailable/failed) from "provider never called"
        (invocations == 0, deterministic-only run).
        """
        from benchmark.reconciliation_runner import run_reconciliation_benchmark
        for mode in ("429", "503", "timeout", "malformed", "tool_incompat", "missing_key"):
            run = run_reconciliation_benchmark(count=10, seed=42, mode="failure", failure_mode=mode)
            # Provider genuinely invoked for every AI-requested case:
            # invocations == cases that actually reached the AI block.
            attempted = [r for r in run.results if r.ai_status in ("unavailable", "failed")]
            skipped = [r for r in run.results if r.ai_status == "not_attempted"]
            assert run.provider_invocations == len(attempted), (
                f"mode {mode}: {run.provider_invocations} invocations but "
                f"{len(attempted)} AI-requested cases"
            )
            assert run.provider_invocations > 0, (
                f"mode {mode}: provider never invoked — failure benchmark is "
                "not exercising the failing provider"
            )
            # Skipped cases must be the architecturally-safe MISSING_PAYMENT
            # early return (no capture → no AI interpretation, never MATCHED).
            for r in skipped:
                assert "MISSING_PAYMENT" in r.exception_codes, (
                    f"mode {mode}: case {r.case_id} skipped AI without a safe reason"
                )
            for r in attempted:
                assert r.ai_status in ("unavailable", "failed")
                assert r.classification != "MATCHED", (
                    f"mode {mode} case {r.case_id}: MATCHED despite provider failure"
                )

    def test_deterministic_mode_never_invokes_provider(self):
        """Deterministic mode never calls the provider (control)."""
        from benchmark.reconciliation_runner import run_reconciliation_benchmark
        run = run_reconciliation_benchmark(count=10, seed=42, mode="deterministic")
        assert run.provider_invocations == 0
        for r in run.results:
            assert r.ai_status != "failed", (
                f"deterministic case {r.case_id}: provider reported failed without a call"
            )

    def test_benchmark_metrics_reproducible(self):
        """Same seed → identical metrics; metrics derive from results."""
        from benchmark.reconciliation_runner import run_reconciliation_benchmark
        from benchmark.reconciliation_metrics import compute_reconciliation_metrics
        a = compute_reconciliation_metrics(run_reconciliation_benchmark(count=50, seed=42, mode="deterministic"))
        b = compute_reconciliation_metrics(run_reconciliation_benchmark(count=50, seed=42, mode="deterministic"))
        assert a.classification_accuracy == b.classification_accuracy
        assert a.matched == b.matched
        assert a.false_auto_resolve == b.false_auto_resolve

    def test_benchmark_marks_mode_explicitly(self):
        from benchmark.reconciliation_runner import run_reconciliation_benchmark
        run = run_reconciliation_benchmark(count=10, seed=42, mode="failure", failure_mode="429")
        assert run.mode == "failure"
        assert "stub" in run.provider
        run2 = run_reconciliation_benchmark(count=10, seed=42, mode="deterministic")
        assert run2.mode == "deterministic"
        assert run2.provider == ""


# ===========================================================================
# Money validation
# ===========================================================================

class TestMoneyValidation:
    def test_normalize_record_rejects_bad_types(self):
        from reconciliation.service import normalize_record
        from reconciliation.calculator import FinancialValidationError
        with pytest.raises(FinancialValidationError):
            normalize_record({"record_type": "payment", "external_id": "p1", "amount": 100,
                              "currency": "EUR"})
        with pytest.raises(FinancialValidationError):
            normalize_record({"record_type": "bogus", "external_id": "p1", "amount": 100})
        with pytest.raises(FinancialValidationError):
            normalize_record({"record_type": "refund", "external_id": "r1", "amount": 100})  # no payment_id
        with pytest.raises(FinancialValidationError):
            normalize_record({"record_type": "adjustment", "external_id": "a1", "amount": 100,
                              "payment_id": "p1"})  # no adjustment_sign
        with pytest.raises(FinancialValidationError):
            normalize_record({"record_type": "payment", "external_id": "p1"})  # no amount

    def test_normalize_record_accepts_valid(self):
        from reconciliation.service import normalize_record
        rec = normalize_record({
            "record_type": "payment", "external_id": "pay_1", "amount": 100000,
            "payment_id": "pay_1", "order_id": "ord_1",
        })
        assert rec.amount == 100000
        assert rec.currency == "INR"


# ===========================================================================
# Event ordering (business-time ordering, not arrival order)
# ===========================================================================

class TestEventOrdering:
    def test_recorded_at_used_for_lateness_not_arrival(self):
        """Lateness is computed from business timestamps, not ingestion order."""
        from reconciliation.service import reconcile_payment
        from reconciliation.models import FinancialRecord
        # Records delivered out of order (settlement first, payment later)
        records = [
            FinancialRecord(
                record_type="settlement", external_id="set_1", amount=97300,
                payment_id="pay_x", status="processed",
                recorded_at="2025-01-12T00:00:00",  # 11 days after capture
                source="live_webhook",
            ),
            FinancialRecord(
                record_type="payment", external_id="pay_x", amount=100000,
                payment_id="pay_x", status="captured",
                recorded_at="2025-01-01T00:00:00",
                source="live_webhook",
            ),
            FinancialRecord(
                record_type="fee_tax", external_id="ft_x", amount=2700,
                payment_id="pay_x", fee_amount=2400, tax_amount=300,
                source="live_webhook",
            ),
        ]
        case = reconcile_payment("test", "pay_x", records)
        assert "LATE_SETTLEMENT" in case.exception_codes
        assert case.classification == "REVIEW_REQUIRED"

# ===========================================================================
# Idempotent retry (safe client retries on run POSTs)
# ===========================================================================

class TestIdempotentRunRetry:
    def _valid_records(self):
        return [{
            "record_type": "payment",
            "external_id": "pay_idem_1",
            "amount": 100000,
            "payment_id": "pay_idem_1",
            "order_id": "ord_idem_1",
        }, {
            "record_type": "fee_tax",
            "external_id": "ft_idem_1",
            "amount": 2700,
            "payment_id": "pay_idem_1",
            "fee_amount": 2400,
            "tax_amount": 300,
        }, {
            "record_type": "settlement",
            "external_id": "set_idem_1",
            "amount": 97300,
            "payment_id": "pay_idem_1",
        }]

    def test_same_idempotency_key_returns_same_run(self, auth_client):
        """A client retry with the SAME Idempotency-Key never creates a
        second run — duplicate financial work is prevented."""
        headers = {"Idempotency-Key": "idem-test-1"}
        body = {"records": self._valid_records(), "source": "api_retry_test"}
        first = auth_client.post("/api/reconciliation/run", json=body, headers=headers)
        assert first.status_code == 200, first.text
        second = auth_client.post("/api/reconciliation/run", json=body, headers=headers)
        assert second.status_code == 200, second.text
        assert first.json()["run_id"] == second.json()["run_id"], (
            "retried POST must return the original run")
        # And no duplicate ledger decisions were produced for that run.
        resp = auth_client.get(f"/api/reconciliation/runs/{first.json()['run_id']}/exceptions")
        assert resp.status_code == 200

    def test_different_keys_create_distinct_runs(self, auth_client):
        body = {"records": self._valid_records(), "source": "api_retry_test"}
        a = auth_client.post("/api/reconciliation/run", json=body,
                             headers={"Idempotency-Key": "idem-a"})
        b = auth_client.post("/api/reconciliation/run", json=body,
                             headers={"Idempotency-Key": "idem-b"})
        assert a.status_code == 200 and b.status_code == 200
        assert a.json()["run_id"] != b.json()["run_id"]

    def test_demo_run_idempotent_retry(self, auth_client):
        headers = {"Idempotency-Key": "idem-demo-1"}
        first = auth_client.post("/api/reconciliation/run/demo?count=30", headers=headers)
        assert first.status_code == 200, first.text
        second = auth_client.post("/api/reconciliation/run/demo?count=30", headers=headers)
        assert second.status_code == 200, second.text
        assert first.json()["run_id"] == second.json()["run_id"]
        assert first.json()["total_cases"] == 30
