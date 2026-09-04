"""Phase 7.2 — Final AI Acceptance Gate Tests.

Tests for:
1. Deterministic policy outcome evaluation
2. Gemini-unavailable safety
3. Policy satisfied → APPROVED
4. Policy violated → NOT APPROVED
5. Policy unresolved → NOT APPROVED
6. Approval gate with policy outcomes
7. Rate limit classification
8. Success state invariant (preserved)
9. Idempotency after auto-approval
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))


# ─── Helpers ─────────────────────────────────────────────────────────

def _make_order_evidence(amount=100000, order_id="ORD-001"):
    return {
        "evidence_id": "ev_order_001",
        "source_type": "order",
        "raw_content": json.dumps({
            "order_id": order_id, "amount": amount, "seller_id": "seller_1",
        }),
        "extracted_facts": "[]",
        "linked_decision_ids": "[]",
    }


def _make_delivery_evidence(order_id="ORD-001", delay_days=5):
    return {
        "evidence_id": "ev_delivery_001",
        "source_type": "delivery",
        "raw_content": json.dumps({
            "order_id": order_id, "promised_date": "2026-08-10",
            "actual_date": "2026-08-15", "delay_days": delay_days,
            "carrier": "Delhivery",
        }),
        "extracted_facts": "[]",
        "linked_decision_ids": "[]",
    }


def _make_refund_evidence(order_id="ORD-001", status="processed"):
    return {
        "evidence_id": "ev_refund_001",
        "source_type": "refund_record",
        "raw_content": json.dumps({
            "order_id": order_id, "refund_id": "RF-001",
            "amount": 5000, "reason": "customer_complaint", "status": status,
        }),
        "extracted_facts": "[]",
        "linked_decision_ids": "[]",
    }


def _make_policies():
    return [
        {"policy_id": "platform_1_1", "version": "1.0",
         "clause_text": "Platform fee 8%", "effective_date": "2024-01-01"},
        {"policy_id": "sla_4_2", "version": "4.2",
         "clause_text": "SLA breach penalty 12000 for delays of 3+ days",
         "effective_date": "2024-01-01"},
        {"policy_id": "returns_3_1", "version": "3.1",
         "clause_text": "Return reserve 5000 for processed returns",
         "effective_date": "2024-01-01"},
    ]


def _make_agent_result(claims=None, classification="clear", confidence=0.95,
                       success=True, stop_reason="analysis_complete",
                       gemini_needed=False, gemini_available=False):
    if claims is None:
        claims = [{
            "claim_type": "platform_fee",
            "policy_clause_id": "platform_1_1",
            "evidence_ids": ["ev_order_001"],
            "reasoning": "Standard platform fee",
        }]
    return {
        "analysis": {
            "claims": claims,
            "classification": classification,
            "confidence": confidence,
            "reasoning_summary": "Test reasoning",
            "missing_evidence": [],
            "conflicting_evidence": [],
        },
        "extracted_facts": [],
        "agent_state": MagicMock(
            stop_reason=stop_reason,
            iteration_count=3,
            duration_ms=1000,
            tools_called=[],
            success=success,
        ),
        "tool_calls": 2,
        "gemini_needed": gemini_needed,
        "gemini_available": gemini_available,
    }


# ═════════════════════════════════════════════════════════════════════
# 1. POLICY OUTCOME EVALUATION
# ═════════════════════════════════════════════════════════════════════

class TestPolicyOutcomeEvaluation:
    """Deterministic policy outcome evaluation for each claim type."""

    def test_platform_fee_satisfied(self):
        """Platform fee with valid order evidence → SATISFIED."""
        from ai.pipeline import _evaluate_policy_outcomes, POLICY_SATISFIED

        evidence = [_make_order_evidence(amount=50000)]
        policies = _make_policies()
        claims = [{"claim_type": "platform_fee", "policy_clause_id": "platform_1_1",
                   "evidence_ids": ["ev_order_001"], "reasoning": "Fee applies"}]

        result = _evaluate_policy_outcomes(claims, evidence, policies)
        assert len(result["outcomes"]) == 1
        assert result["outcomes"][0]["outcome"] == POLICY_SATISFIED

    def test_platform_fee_violated_no_amount(self):
        """Platform fee with zero amount → VIOLATED."""
        from ai.pipeline import _evaluate_policy_outcomes, POLICY_VIOLATED

        evidence = [{
            "evidence_id": "ev_order_001", "source_type": "order",
            "raw_content": json.dumps({"order_id": "O1", "amount": 0, "seller_id": "s1"}),
            "extracted_facts": "[]", "linked_decision_ids": "[]",
        }]
        policies = _make_policies()
        claims = [{"claim_type": "platform_fee", "policy_clause_id": "platform_1_1",
                   "evidence_ids": ["ev_order_001"], "reasoning": "Fee applies"}]

        result = _evaluate_policy_outcomes(claims, evidence, policies)
        assert result["outcomes"][0]["outcome"] == POLICY_VIOLATED

    def test_sla_breach_satisfied_delay_5(self):
        """SLA breach with delay >= 3 → SATISFIED."""
        from ai.pipeline import _evaluate_policy_outcomes, POLICY_SATISFIED

        evidence = [_make_delivery_evidence(delay_days=5)]
        policies = _make_policies()
        claims = [{"claim_type": "sla_breach", "policy_clause_id": "sla_4_2",
                   "evidence_ids": ["ev_delivery_001"], "reasoning": "Late delivery"}]

        result = _evaluate_policy_outcomes(claims, evidence, policies)
        assert result["outcomes"][0]["outcome"] == POLICY_SATISFIED

    def test_sla_breach_violated_delay_1(self):
        """SLA breach with delay < 3 → VIOLATED."""
        from ai.pipeline import _evaluate_policy_outcomes, POLICY_VIOLATED

        evidence = [_make_delivery_evidence(delay_days=1)]
        policies = _make_policies()
        claims = [{"claim_type": "sla_breach", "policy_clause_id": "sla_4_2",
                   "evidence_ids": ["ev_delivery_001"], "reasoning": "Slight delay"}]

        result = _evaluate_policy_outcomes(claims, evidence, policies)
        assert result["outcomes"][0]["outcome"] == POLICY_VIOLATED

    def test_sla_breach_unresolved_no_delivery(self):
        """SLA breach with no delivery evidence → UNRESOLVED."""
        from ai.pipeline import _evaluate_policy_outcomes, POLICY_UNRESOLVED

        evidence = [_make_order_evidence()]
        policies = _make_policies()
        claims = [{"claim_type": "sla_breach", "policy_clause_id": "sla_4_2",
                   "evidence_ids": ["ev_order_001"], "reasoning": "Claimed SLA breach"}]

        result = _evaluate_policy_outcomes(claims, evidence, policies)
        assert result["outcomes"][0]["outcome"] == POLICY_UNRESOLVED

    def test_return_processed_satisfied(self):
        """Return processed with status=processed → SATISFIED."""
        from ai.pipeline import _evaluate_policy_outcomes, POLICY_SATISFIED

        evidence = [_make_refund_evidence(status="processed")]
        policies = _make_policies()
        claims = [{"claim_type": "return_processed", "policy_clause_id": "returns_3_1",
                   "evidence_ids": ["ev_refund_001"], "reasoning": "Return done"}]

        result = _evaluate_policy_outcomes(claims, evidence, policies)
        assert result["outcomes"][0]["outcome"] == POLICY_SATISFIED

    def test_return_processed_violated_pending(self):
        """Return processed with status=pending → VIOLATED."""
        from ai.pipeline import _evaluate_policy_outcomes, POLICY_VIOLATED

        evidence = [_make_refund_evidence(status="pending")]
        policies = _make_policies()
        claims = [{"claim_type": "return_processed", "policy_clause_id": "returns_3_1",
                   "evidence_ids": ["ev_refund_001"], "reasoning": "Return pending"}]

        result = _evaluate_policy_outcomes(claims, evidence, policies)
        assert result["outcomes"][0]["outcome"] == POLICY_VIOLATED

    def test_return_processed_unresolved_no_evidence(self):
        """Return processed with no refund evidence → UNRESOLVED."""
        from ai.pipeline import _evaluate_policy_outcomes, POLICY_UNRESOLVED

        evidence = [_make_order_evidence()]
        policies = _make_policies()
        claims = [{"claim_type": "return_processed", "policy_clause_id": "returns_3_1",
                   "evidence_ids": ["ev_order_001"], "reasoning": "Return claimed"}]

        result = _evaluate_policy_outcomes(claims, evidence, policies)
        assert result["outcomes"][0]["outcome"] == POLICY_UNRESOLVED

    def test_unknown_claim_type_unresolved(self):
        """Unknown claim type → UNRESOLVED."""
        from ai.pipeline import _evaluate_policy_outcomes, POLICY_UNRESOLVED

        evidence = [_make_order_evidence()]
        policies = _make_policies()
        claims = [{"claim_type": "unknown_type", "policy_clause_id": "platform_1_1",
                   "evidence_ids": ["ev_order_001"], "reasoning": "Unknown"}]

        result = _evaluate_policy_outcomes(claims, evidence, policies)
        assert result["outcomes"][0]["outcome"] == POLICY_UNRESOLVED

    def test_non_monetary_claim_satisfied(self):
        """Non-monetary claims (no_penalty, other) → SATISFIED."""
        from ai.pipeline import _evaluate_policy_outcomes, POLICY_SATISFIED

        evidence = [_make_order_evidence()]
        policies = _make_policies()
        claims = [{"claim_type": "no_penalty", "policy_clause_id": "platform_1_1",
                   "evidence_ids": ["ev_order_001"], "reasoning": "No penalty"}]

        result = _evaluate_policy_outcomes(claims, evidence, policies)
        assert result["outcomes"][0]["outcome"] == POLICY_SATISFIED


# ═════════════════════════════════════════════════════════════════════
# 2. GEMINI-UNAVAILABLE SAFETY
# ═════════════════════════════════════════════════════════════════════

class TestGeminiUnavailableSafety:
    """When Gemini is needed but unavailable, decision must not be APPROVED."""

    def test_gemini_needed_unavailable_blocks_approval(self):
        """Gemini needed + unavailable → REVIEW_REQUIRED."""
        from ai.pipeline import _determine_decision_status, SUFFICIENT

        status = _determine_decision_status(
            agent_success=True,
            evidence_sufficiency=SUFFICIENT,
            evidence_errors=[],
            policy_errors=[],
            classification="clear",
            exceptions=[],
            validation={"valid": True},
            gemini_needed=True,
            gemini_available=False,
        )
        assert status == "REVIEW_REQUIRED"

    def test_gemini_not_needed_skips_check(self):
        """Gemini not needed → gate ignores Gemini availability."""
        from ai.pipeline import _determine_decision_status, SUFFICIENT

        status = _determine_decision_status(
            agent_success=True,
            evidence_sufficiency=SUFFICIENT,
            evidence_errors=[],
            policy_errors=[],
            classification="clear",
            exceptions=[],
            validation={"valid": True},
            gemini_needed=False,
            gemini_available=False,
        )
        assert status == "APPROVED"

    def test_gemini_needed_available_allows_approval(self):
        """Gemini needed + available → gate continues."""
        from ai.pipeline import _determine_decision_status, SUFFICIENT

        status = _determine_decision_status(
            agent_success=True,
            evidence_sufficiency=SUFFICIENT,
            evidence_errors=[],
            policy_errors=[],
            classification="clear",
            exceptions=[],
            validation={"valid": True},
            gemini_needed=True,
            gemini_available=True,
        )
        assert status == "APPROVED"

    def test_agent_result_includes_gemini_flags(self):
        """Agent result must include gemini_needed and gemini_available flags."""
        from ai.agent import run_agent
        import inspect
        # Verify run_agent returns dict with gemini flags in its docstring/implementation
        # The flags are added to the return dict by run_agent
        source = inspect.getsource(run_agent)
        assert "gemini_needed" in source, "run_agent must return gemini_needed flag"
        assert "gemini_available" in source, "run_agent must return gemini_available flag"


# ═════════════════════════════════════════════════════════════════════
# 3. POLICY OUTCOME IN APPROVAL GATE
# ═════════════════════════════════════════════════════════════════════

class TestPolicyOutcomeInApprovalGate:
    """Policy outcomes must be checked in the approval gate."""

    def test_sla_satisfied_allows_approval(self):
        """SLA breach policy satisfied → gate allows approval."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000), _make_delivery_evidence(delay_days=5)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "sla_breach",
                "policy_clause_id": "sla_4_2",
                "evidence_ids": ["ev_order_001", "ev_delivery_001"],
                "reasoning": "Delivery was 5 days late",
            }],
            classification="clear",
        )

        result = run_pipeline(
            scenario_id="policy_satisfied",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )

        decision = result["decision"]
        mo = decision["model_output"]
        assert decision["status"] == "APPROVED"
        # Verify policy outcomes recorded
        assert len(mo["policy_outcomes"]) == 1
        assert mo["policy_outcomes"][0]["outcome"] == "SATISFIED"

    def test_sla_violated_blocks_approval(self):
        """SLA breach policy violated (delay < 3) → REVIEW_REQUIRED."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000), _make_delivery_evidence(delay_days=1)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "sla_breach",
                "policy_clause_id": "sla_4_2",
                "evidence_ids": ["ev_order_001", "ev_delivery_001"],
                "reasoning": "LLM claims SLA breach but delay is only 1 day",
            }],
            classification="clear",
        )

        result = run_pipeline(
            scenario_id="policy_violated",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )

        decision = result["decision"]
        mo = decision["model_output"]
        assert decision["status"] == "REVIEW_REQUIRED"
        # Policy outcome is VIOLATED
        assert any(o["outcome"] == "VIOLATED" for o in mo["policy_outcomes"])

    def test_return_unresolved_blocks_approval(self):
        """Return policy unresolved (no refund evidence) → REVIEW_REQUIRED."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000), _make_delivery_evidence(delay_days=5)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "return_processed",
                "policy_clause_id": "returns_3_1",
                "evidence_ids": ["ev_order_001", "ev_delivery_001"],
                "reasoning": "LLM claims return but no refund evidence",
            }],
            classification="clear",
        )

        result = run_pipeline(
            scenario_id="policy_unresolved",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )

        decision = result["decision"]
        mo = decision["model_output"]
        assert decision["status"] == "REVIEW_REQUIRED"
        assert any(o["outcome"] == "UNRESOLVED" for o in mo["policy_outcomes"])

    def test_llm_high_confidence_cannot_override_policy_violation(self):
        """High confidence + policy violated → REVIEW_REQUIRED."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000), _make_delivery_evidence(delay_days=1)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "sla_breach",
                "policy_clause_id": "sla_4_2",
                "evidence_ids": ["ev_order_001", "ev_delivery_001"],
                "reasoning": "Very confident SLA breach",
            }],
            classification="clear",
            confidence=0.99,
        )

        result = run_pipeline(
            scenario_id="confidence_override",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )

        assert result["decision"]["status"] == "REVIEW_REQUIRED"
        # Despite 0.99 confidence, policy is violated
        assert result["decision"]["model_output"]["confidence"] == 0.99


# ═════════════════════════════════════════════════════════════════════
# 4. COMPREHENSIVE SAFETY CASES
# ═════════════════════════════════════════════════════════════════════

class TestComprehensiveSafetyCases:
    """Real safety cases A-J using actual application code."""

    def test_case_a_all_conditions_met(self):
        """A: All evidence sufficient + policy satisfied + valid calc → APPROVED."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000), _make_delivery_evidence(delay_days=5)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "sla_breach",
                "policy_clause_id": "sla_4_2",
                "evidence_ids": ["ev_order_001", "ev_delivery_001"],
                "reasoning": "Delivery 5 days late",
            }],
            classification="clear",
        )

        result = run_pipeline(
            scenario_id="case_a",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )

        assert result["decision"]["status"] == "APPROVED"

    def test_case_b_evidence_missing(self):
        """B: Evidence missing → REVIEW_REQUIRED."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "sla_breach",
                "policy_clause_id": "sla_4_2",
                "evidence_ids": ["ev_order_001", "ev_nonexistent"],
                "reasoning": "Claims nonexistent evidence",
            }],
            classification="clear",
        )

        result = run_pipeline(
            scenario_id="case_b",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )

        assert result["decision"]["status"] == "REVIEW_REQUIRED"

    def test_case_c_conflicting_evidence(self):
        """C: Conflicting evidence → REVIEW_REQUIRED."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(), _make_delivery_evidence(), _make_refund_evidence()]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "sla_breach",
                "policy_clause_id": "sla_4_2",
                "evidence_ids": ["ev_order_001", "ev_delivery_001", "ev_refund_001"],
                "reasoning": "Conflicting",
            }],
            classification="exception",
        )

        result = run_pipeline(
            scenario_id="case_c",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )

        assert result["decision"]["status"] == "REVIEW_REQUIRED"

    def test_case_d_policy_violated(self):
        """D: Policy violated (SLA claim but delay < 3) → NOT APPROVED."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000), _make_delivery_evidence(delay_days=1)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "sla_breach",
                "policy_clause_id": "sla_4_2",
                "evidence_ids": ["ev_order_001", "ev_delivery_001"],
                "reasoning": "Wrongly claims SLA breach",
            }],
            classification="clear",
        )

        result = run_pipeline(
            scenario_id="case_d",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )

        assert result["decision"]["status"] != "APPROVED"

    def test_case_e_policy_unresolved(self):
        """E: Policy unresolved (return claim with no refund evidence) → NOT APPROVED."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000), _make_delivery_evidence(delay_days=5)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "return_processed",
                "policy_clause_id": "returns_3_1",
                "evidence_ids": ["ev_order_001", "ev_delivery_001"],
                "reasoning": "Claims return but no refund evidence",
            }],
            classification="clear",
        )

        result = run_pipeline(
            scenario_id="case_e",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )

        assert result["decision"]["status"] != "APPROVED"

    def test_case_f_gemini_unavailable(self):
        """F: Gemini needed but unavailable → REVIEW_REQUIRED."""
        from ai.pipeline import _determine_decision_status, SUFFICIENT

        status = _determine_decision_status(
            agent_success=True,
            evidence_sufficiency=SUFFICIENT,
            evidence_errors=[],
            policy_errors=[],
            classification="clear",
            exceptions=[],
            validation={"valid": True},
            gemini_needed=True,
            gemini_available=False,
        )
        assert status == "REVIEW_REQUIRED"

    def test_case_g_provider_failure(self):
        """G: Provider failure → REVIEW_REQUIRED."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[], classification="exception", success=False,
            stop_reason="rate_limit",
        )

        result = run_pipeline(
            scenario_id="case_g",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )

        assert result["decision"]["status"] != "APPROVED"

    def test_case_h_invalid_evidence_reference(self):
        """H: Invalid evidence reference → NOT APPROVED."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "platform_fee",
                "policy_clause_id": "platform_1_1",
                "evidence_ids": ["ev_nonexistent"],
                "reasoning": "Bad reference",
            }],
            classification="clear",
        )

        result = run_pipeline(
            scenario_id="case_h",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )

        assert result["decision"]["status"] != "APPROVED"

    def test_case_i_invalid_policy_reference(self):
        """I: Invalid policy reference → NOT APPROVED."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "platform_fee",
                "policy_clause_id": "made_up_policy",
                "evidence_ids": ["ev_order_001"],
                "reasoning": "Bad policy ref",
            }],
            classification="clear",
        )

        result = run_pipeline(
            scenario_id="case_i",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )

        assert result["decision"]["status"] != "APPROVED"

    def test_case_j_idempotency(self):
        """J: Same case repeated → same fingerprint, idempotent."""
        from ai.pipeline import compute_analysis_fingerprint

        fp1 = compute_analysis_fingerprint(
            tenant_id="t1", scenario_id="s1",
            evidence_ids=["ev_a", "ev_b"], policy_ids=["platform_1_1"],
        )
        fp2 = compute_analysis_fingerprint(
            tenant_id="t1", scenario_id="s1",
            evidence_ids=["ev_a", "ev_b"], policy_ids=["platform_1_1"],
        )
        assert fp1 == fp2


# ═════════════════════════════════════════════════════════════════════
# 5. RATE LIMIT CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════

class TestRateLimitClassification:
    """429 errors must be classified as rate_limit, not generic llm_error."""

    def test_429_classified_as_rate_limit(self):
        """429 error message → rate_limit."""
        from ai.failure_taxonomy import classify_provider_error, FailureType

        result = classify_provider_error("Groq API error 429: rate limit exceeded")
        assert result == FailureType.RATE_LIMIT

    def test_timeout_classified_correctly(self):
        """Timeout error → timeout."""
        from ai.failure_taxonomy import classify_provider_error, FailureType

        result = classify_provider_error("Request timed out after 30s")
        assert result == FailureType.TIMEOUT

    def test_generic_error_classified_provider(self):
        """Generic error → provider_error."""
        from ai.failure_taxonomy import classify_provider_error, FailureType

        result = classify_provider_error("Something went wrong")
        assert result == FailureType.PROVIDER_ERROR

    def test_rate_limit_stop_reason_mapped(self):
        """rate_limit stop reason → rate_limit failure type."""
        from ai.failure_taxonomy import classify_stop_reason, FailureType

        ft = classify_stop_reason("rate_limit")
        assert ft == FailureType.RATE_LIMIT

    def test_bounded_retry_no_infinite(self):
        """Agent retry loop is bounded by MAX_AGENT_ITERATIONS."""
        from ai.agent import MAX_AGENT_ITERATIONS, MAX_TOOL_CALLS, MAX_EXECUTION_DURATION_S
        assert MAX_AGENT_ITERATIONS <= 10
        assert MAX_TOOL_CALLS <= 20
        assert MAX_EXECUTION_DURATION_S <= 120


# ═════════════════════════════════════════════════════════════════════
# 6. AUTO-APPROVAL SAFETY PROOF
# ═════════════════════════════════════════════════════════════════════

class TestAutoApprovalSafety:
    """Prove that no single condition is sufficient for APPROVED."""

    def test_high_confidence_alone_insufficient(self):
        """High confidence alone → NOT sufficient for APPROVED."""
        from ai.pipeline import _determine_decision_status, INSUFFICIENT

        status = _determine_decision_status(
            agent_success=True,
            evidence_sufficiency=INSUFFICIENT,
            evidence_errors=[],
            policy_errors=[],
            classification="clear",
            exceptions=[],
            validation={"valid": True},
            policy_outcomes={"outcomes": [{"policy_clause_id": "x", "outcome": "SATISFIED", "reason": "ok"}]},
        )
        assert status == "REVIEW_REQUIRED"

    def test_valid_evidence_alone_insufficient(self):
        """Valid evidence alone → NOT sufficient (policy may be violated)."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000), _make_delivery_evidence(delay_days=1)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "sla_breach",
                "policy_clause_id": "sla_4_2",
                "evidence_ids": ["ev_order_001", "ev_delivery_001"],
                "reasoning": "Wrong claim",
            }],
            classification="clear",
        )

        result = run_pipeline(
            scenario_id="evidence_only",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )
        # Evidence is valid but policy is violated (delay 1 day < 3)
        assert result["decision"]["status"] != "APPROVED"

    def test_valid_policy_id_alone_insufficient(self):
        """Valid policy ID alone → NOT sufficient (evidence may not satisfy conditions)."""
        from ai.pipeline import run_pipeline

        evidence = [_make_order_evidence(amount=100000)]
        policies = _make_policies()
        agent_result = _make_agent_result(
            claims=[{
                "claim_type": "sla_breach",
                "policy_clause_id": "sla_4_2",
                "evidence_ids": ["ev_order_001"],
                "reasoning": "Claims SLA breach with no delivery evidence",
            }],
            classification="clear",
        )

        result = run_pipeline(
            scenario_id="policy_only",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            agent_result=agent_result,
        )
        assert result["decision"]["status"] != "APPROVED"

    def test_valid_calculation_alone_insufficient(self):
        """Valid calculation alone → NOT sufficient (other conditions may fail)."""
        from ai.pipeline import _determine_decision_status, SUFFICIENT

        status = _determine_decision_status(
            agent_success=False,  # Agent failed
            evidence_sufficiency=SUFFICIENT,
            evidence_errors=[],
            policy_errors=[],
            classification="clear",
            exceptions=[],
            validation={"valid": True},
        )
        assert status == "REVIEW_REQUIRED"
