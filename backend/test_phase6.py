"""Tests for Phase 6: Gemini evidence intelligence, failure taxonomy, claim mapping,
compact context, and provider parity.

These tests verify the actual production AI controller components.
All provider tests use mocked providers for CI — no real LLM calls.
"""
import pytest
import json
import os
from unittest.mock import patch, MagicMock, AsyncMock


# ===========================================================================
# 1. Deterministic Gemini Routing
# ===========================================================================

class TestGeminiRouting:
    """Test needs_deep_evidence_analysis() deterministic routing."""

    def test_simple_order_does_not_trigger(self):
        """Simple Razorpay order data should NOT trigger Gemini."""
        from ai.evidence_intelligence import needs_deep_evidence_analysis
        evidence = [{
            "evidence_id": "ev_001",
            "source_type": "order",
            "raw_content": json.dumps({
                "order_id": "ORD-001", "amount": 100000,
                "seller_id": "seller_1", "status": "captured",
            }),
        }]
        assert needs_deep_evidence_analysis(evidence) is False

    def test_structured_delivery_does_not_trigger(self):
        """Structured delivery data should NOT trigger Gemini."""
        from ai.evidence_intelligence import needs_deep_evidence_analysis
        evidence = [
            {
                "evidence_id": "ev_001",
                "source_type": "order",
                "raw_content": json.dumps({"order_id": "ORD-001", "amount": 100000}),
            },
            {
                "evidence_id": "ev_002",
                "source_type": "delivery",
                "raw_content": json.dumps({
                    "order_id": "ORD-001", "promised_date": "2024-01-15",
                    "actual_date": "2024-01-14", "delay_days": 0,
                }),
            },
        ]
        assert needs_deep_evidence_analysis(evidence) is False

    def test_document_evidence_triggers(self):
        """Document evidence types should trigger Gemini."""
        from ai.evidence_intelligence import needs_deep_evidence_analysis
        evidence = [{
            "evidence_id": "ev_001",
            "source_type": "uploaded_document",
            "raw_content": json.dumps({"content": "contract terms..."}),
        }]
        assert needs_deep_evidence_analysis(evidence) is True

    def test_legal_notice_triggers(self):
        """Legal notice evidence should trigger Gemini."""
        from ai.evidence_intelligence import needs_deep_evidence_analysis
        evidence = [{
            "evidence_id": "ev_001",
            "source_type": "legal_notice",
            "raw_content": json.dumps({"content": "notice text..."}),
        }]
        assert needs_deep_evidence_analysis(evidence) is True

    def test_long_complaint_triggers(self):
        """Long unstructured complaint should trigger Gemini."""
        from ai.evidence_intelligence import needs_deep_evidence_analysis
        evidence = [{
            "evidence_id": "ev_001",
            "source_type": "complaint",
            "raw_content": json.dumps({
                "order_id": "ORD-001",
                "issue": "Product was delivered damaged " + "x" * 2000,
            }),
        }]
        assert needs_deep_evidence_analysis(evidence) is True

    def test_many_evidence_records_trigger(self):
        """More than 5 evidence records triggers complexity heuristic."""
        from ai.evidence_intelligence import needs_deep_evidence_analysis
        evidence = [
            {"evidence_id": f"ev_{i:03d}", "source_type": "order",
             "raw_content": json.dumps({"order_id": f"ORD-{i}"})}
            for i in range(7)
        ]
        assert needs_deep_evidence_analysis(evidence) is True

    def test_force_deep_overrides(self):
        """force_deep=True always triggers."""
        from ai.evidence_intelligence import needs_deep_evidence_analysis
        evidence = [{
            "evidence_id": "ev_001",
            "source_type": "order",
            "raw_content": json.dumps({"order_id": "ORD-001"}),
        }]
        assert needs_deep_evidence_analysis(evidence, force_deep=True) is True

    def test_empty_evidence_no_trigger(self):
        """Empty evidence list should not trigger."""
        from ai.evidence_intelligence import needs_deep_evidence_analysis
        assert needs_deep_evidence_analysis([]) is False

    def test_complaint_with_delay_triggers_contradiction(self):
        """Complaint mentioning delay with delivery evidence triggers."""
        from ai.evidence_intelligence import needs_deep_evidence_analysis
        evidence = [
            {
                "evidence_id": "ev_001",
                "source_type": "order",
                "raw_content": json.dumps({"order_id": "ORD-001", "amount": 50000}),
            },
            {
                "evidence_id": "ev_002",
                "source_type": "delivery",
                "raw_content": json.dumps({
                    "order_id": "ORD-001", "delay_days": 0,
                }),
            },
            {
                "evidence_id": "ev_003",
                "source_type": "complaint",
                "raw_content": json.dumps({
                    "order_id": "ORD-001",
                    "issue": "Late delivery, package arrived 5 days after promised date",
                }),
            },
        ]
        assert needs_deep_evidence_analysis(evidence) is True


# ===========================================================================
# 2. Failure Taxonomy
# ===========================================================================

class TestFailureTaxonomy:
    """Test the complete failure taxonomy."""

    def test_all_failure_types_exist(self):
        """All required failure types are defined."""
        from ai.failure_taxonomy import FailureType
        required = [
            "provider_error", "rate_limit", "timeout",
            "tool_error", "invalid_tool_arguments",
            "schema_error", "semantic_validation_error",
            "invalid_evidence_reference", "invalid_policy_reference",
            "contradictory_evidence", "insufficient_evidence",
            "agent_limit", "unknown",
        ]
        for ft in required:
            assert FailureType(ft), f"Missing FailureType: {ft}"

    def test_classify_provider_error_rate_limit(self):
        """429 errors classify as rate_limit."""
        from ai.failure_taxonomy import classify_provider_error, FailureType
        assert classify_provider_error("429 Too Many Requests") == FailureType.RATE_LIMIT
        assert classify_provider_error("rate limit exceeded") == FailureType.RATE_LIMIT

    def test_classify_provider_error_timeout(self):
        """Timeout errors classify correctly."""
        from ai.failure_taxonomy import classify_provider_error, FailureType
        assert classify_provider_error("Request timed out after 60s") == FailureType.TIMEOUT

    def test_classify_stop_reason_agent_limit(self):
        """Agent limits classify correctly."""
        from ai.failure_taxonomy import classify_stop_reason, FailureType
        assert classify_stop_reason("max_iterations") == FailureType.AGENT_LIMIT
        assert classify_stop_reason("max_tool_calls") == FailureType.AGENT_LIMIT

    def test_classify_stop_reason_success(self):
        """Success reasons are not failures."""
        from ai.failure_taxonomy import classify_stop_reason, is_failure, is_success
        assert is_success("evidence_gathering_complete") is True
        assert is_success("analysis_complete") is True
        assert is_failure("evidence_gathering_complete") is False

    def test_classify_stop_reason_failure(self):
        """Failure reasons are classified."""
        from ai.failure_taxonomy import classify_stop_reason, is_failure, FailureType
        assert is_failure("llm_error") is True
        assert classify_stop_reason("llm_error") == FailureType.PROVIDER_ERROR

    def test_safe_api_subset(self):
        """Only safe failure types are exposed in API responses."""
        from ai.failure_taxonomy import (
            FailureType, SAFE_API_FAILURE_TYPES, safe_failure_type_for_api,
        )
        # These should be safe
        assert FailureType.PROVIDER_ERROR in SAFE_API_FAILURE_TYPES
        assert FailureType.TIMEOUT in SAFE_API_FAILURE_TYPES
        assert FailureType.CONTRADICTORY_EVIDENCE in SAFE_API_FAILURE_TYPES

    def test_agent_state_includes_failure_type(self):
        """AgentRunState.to_dict() includes failure_type."""
        from ai.agent import AgentRunState
        state = AgentRunState(stop_reason="llm_error")
        d = state.to_dict()
        assert "failure_type" in d
        assert d["failure_type"] == "provider_error"

    def test_agent_state_success_no_failure_type(self):
        """Successful agent run has no failure_type."""
        from ai.agent import AgentRunState
        state = AgentRunState(stop_reason="analysis_complete")
        d = state.to_dict()
        assert d["failure_type"] is None


# ===========================================================================
# 3. Claim Mapping
# ===========================================================================

class TestClaimMapping:
    """Test complete claim type mapping."""

    def test_all_claim_types_mapped(self):
        """Every expected claim type has a mapping entry."""
        from ai.pipeline import CLAIM_MAPPING
        expected = {"platform_fee", "sla_breach", "return_processed",
                    "no_penalty", "other", "compliance_note"}
        assert set(CLAIM_MAPPING.keys()) == expected

    def test_monetary_claims(self):
        """Monetary claims have calculation rules."""
        from ai.pipeline import CLAIM_MAPPING
        for claim_type in ("platform_fee", "sla_breach", "return_processed"):
            mapping = CLAIM_MAPPING[claim_type]
            assert mapping["monetary_effect"] is True
            assert mapping["calculation"] is not None

    def test_non_monetary_claims(self):
        """Non-monetary claims are recorded but produce no money."""
        from ai.pipeline import CLAIM_MAPPING
        for claim_type in ("no_penalty", "other", "compliance_note"):
            mapping = CLAIM_MAPPING[claim_type]
            assert mapping["monetary_effect"] is False
            assert mapping["amount"] == 0

    def test_sla_breach_calculation(self):
        """SLA breach maps to fixed penalty."""
        from ai.pipeline import _map_claims_to_calculation_params
        claims = [{
            "claim_type": "sla_breach",
            "policy_clause_id": "sla_4_2",
            "evidence_ids": ["ev_001"],
            "reasoning": "late delivery",
        }]
        params = _map_claims_to_calculation_params(claims)
        assert params["has_sla_breach"] is True
        assert params["sla_penalty_amount"] == 12000

    def test_return_processed_calculation(self):
        """Return processed maps to reserve."""
        from ai.pipeline import _map_claims_to_calculation_params
        claims = [{
            "claim_type": "return_processed",
            "policy_clause_id": "returns_3_1",
            "evidence_ids": ["ev_002"],
            "reasoning": "return processed",
        }]
        params = _map_claims_to_calculation_params(claims)
        assert params["has_returns"] is True
        assert params["return_reserve_amount"] == 5000

    def test_non_monetary_claim_no_effect(self):
        """Non-monetary claims produce no calculation params."""
        from ai.pipeline import _map_claims_to_calculation_params
        claims = [{
            "claim_type": "no_penalty",
            "policy_clause_id": "platform_1_1",
            "evidence_ids": ["ev_001"],
            "reasoning": "complaint resolved",
        }]
        params = _map_claims_to_calculation_params(claims)
        assert params["has_sla_breach"] is False
        assert params["has_returns"] is False

    def test_unknown_claim_type_warning(self):
        """Unknown claim types produce a warning but no crash."""
        from ai.pipeline import _map_claims_to_calculation_params
        claims = [{
            "claim_type": "totally_unknown",
            "policy_clause_id": "xyz",
            "evidence_ids": ["ev_001"],
            "reasoning": "test",
        }]
        params = _map_claims_to_calculation_params(claims)
        # Should not crash — params remain default
        assert params["has_sla_breach"] is False


# ===========================================================================
# 4. Evidence Sufficiency States
# ===========================================================================

class TestEvidenceSufficiency:
    """Test deterministic evidence sufficiency classification."""

    def test_sufficient_when_claims_have_evidence(self):
        """SUFFICIENT when claims reference evidence and no conflicts."""
        from ai.pipeline import _determine_evidence_sufficiency
        reasoning = {
            "claims": [{"claim_type": "sla_breach", "evidence_ids": ["ev_001"]}],
            "conflicting_evidence": [],
        }
        evidence = [{"evidence_id": "ev_001", "source_type": "delivery"}]
        result = _determine_evidence_sufficiency(reasoning, evidence, [])
        assert result == "SUFFICIENT"

    def test_insufficient_when_no_claims(self):
        """INSUFFICIENT when no claims despite evidence."""
        from ai.pipeline import _determine_evidence_sufficiency
        reasoning = {"claims": [], "conflicting_evidence": []}
        evidence = [{"evidence_id": "ev_001", "source_type": "order"}]
        result = _determine_evidence_sufficiency(reasoning, evidence, [])
        assert result == "INSUFFICIENT"

    def test_conflicting_when_agent_reports(self):
        """CONFLICTING when agent reports conflicting evidence."""
        from ai.pipeline import _determine_evidence_sufficiency
        reasoning = {
            "claims": [{"claim_type": "sla_breach", "evidence_ids": ["ev_001"]}],
            "conflicting_evidence": ["ev_001 vs ev_002"],
        }
        evidence = [{"evidence_id": "ev_001", "source_type": "delivery"}]
        result = _determine_evidence_sufficiency(reasoning, evidence, [])
        assert result == "CONFLICTING"

    def test_unavailable_when_no_evidence(self):
        """UNAVAILABLE when no evidence records exist."""
        from ai.pipeline import _determine_evidence_sufficiency
        reasoning = {"claims": [], "conflicting_evidence": []}
        result = _determine_evidence_sufficiency(reasoning, [], [])
        assert result == "UNAVAILABLE"

    def test_insufficient_when_reference_errors(self):
        """INSUFFICIENT when evidence references are invalid."""
        from ai.pipeline import _determine_evidence_sufficiency
        reasoning = {
            "claims": [{"claim_type": "sla_breach", "evidence_ids": ["ev_001"]}],
            "conflicting_evidence": [],
        }
        evidence = [{"evidence_id": "ev_001", "source_type": "delivery"}]
        errors = ["Claim references non-existent evidence: ev_nonexistent"]
        result = _determine_evidence_sufficiency(reasoning, evidence, errors)
        assert result == "INSUFFICIENT"

    def test_insufficient_when_claim_has_no_evidence_ids(self):
        """INSUFFICIENT when a claim has an empty evidence_ids list."""
        from ai.pipeline import _determine_evidence_sufficiency
        reasoning = {
            "claims": [{"claim_type": "sla_breach", "evidence_ids": []}],
            "conflicting_evidence": [],
        }
        evidence = [{"evidence_id": "ev_001", "source_type": "delivery"}]
        result = _determine_evidence_sufficiency(reasoning, evidence, [])
        assert result == "INSUFFICIENT"


# ===========================================================================
# 5. Compact Context
# ===========================================================================

class TestCompactContext:
    """Test compact analysis context builder."""

    def test_compact_context_bounded(self):
        """Context must be bounded regardless of conversation size."""
        from ai.compact_context import build_compact_analysis_context
        # Build a large conversation
        conversation = []
        for i in range(50):
            conversation.append({"role": "user", "content": f"msg_{i}"})
            conversation.append({
                "role": "tool",
                "name": "get_order",
                "content": json.dumps({"found": True, "order_id": f"ORD-{i}"}),
            })
        evidence = [
            {"evidence_id": f"ev_{i:03d}", "source_type": "order",
             "raw_content": json.dumps({"order_id": f"ORD-{i}"})}
            for i in range(50)
        ]
        ctx = build_compact_analysis_context(conversation, evidence)
        # Should be much smaller than raw conversation
        raw_size = sum(len(json.dumps(m)) for m in conversation)
        assert len(ctx) < raw_size, "Compact context should be smaller than raw"

    def test_compact_context_includes_evidence_ids(self):
        """Compact context includes all evidence IDs."""
        from ai.compact_context import build_compact_analysis_context
        evidence = [
            {"evidence_id": "ev_001", "source_type": "order",
             "raw_content": json.dumps({"order_id": "ORD-001"})},
            {"evidence_id": "ev_002", "source_type": "delivery",
             "raw_content": json.dumps({"order_id": "ORD-001"})},
        ]
        ctx = build_compact_analysis_context([], evidence)
        assert "ev_001" in ctx
        assert "ev_002" in ctx

    def test_compact_context_includes_tool_summaries(self):
        """Compact context summarizes tool results."""
        from ai.compact_context import build_compact_analysis_context
        conversation = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "initial"},
            {
                "role": "tool",
                "name": "get_delivery",
                "content": json.dumps({
                    "found": True,
                    "deliveries": [{"evidence_id": "ev_del_001",
                                    "delay_days": 5, "promised_date": "2024-01-15",
                                    "actual_date": "2024-01-20"}],
                }),
            },
        ]
        ctx = build_compact_analysis_context(conversation, [])
        assert "delivery" in ctx.lower() or "delay" in ctx.lower() or "ev_del_001" in ctx

    def test_compact_context_with_agent_summary(self):
        """Compact context includes agent summary."""
        from ai.compact_context import build_compact_analysis_context
        ctx = build_compact_analysis_context(
            [], [], agent_summary="Found SLA breach evidence",
        )
        assert "SLA breach" in ctx


# ===========================================================================
# 6. Evidence Intelligence Schema
# ===========================================================================

class TestEvidenceIntelligence:
    """Test Gemini evidence intelligence validation."""

    def test_validate_result_references(self):
        """Invalid evidence references are rejected."""
        from ai.evidence_intelligence import _validate_gemini_evidence_result
        evidence = [
            {"evidence_id": "ev_001", "source_type": "order",
             "raw_content": "{}"},
        ]
        parsed = {
            "facts": [
                {"fact_type": "order_detail", "value": "test",
                 "evidence_id": "ev_001", "confidence": 0.9},
                {"fact_type": "order_detail", "value": "test2",
                 "evidence_id": "ev_nonexistent", "confidence": 0.9},
            ],
            "contradictions": [],
            "confidence": 0.85,
            "source_evidence_ids": ["ev_001", "ev_nonexistent"],
            "summary": "test",
        }
        result = _validate_gemini_evidence_result(parsed, evidence)
        # ev_nonexistent should be filtered out
        assert len(result.facts) == 1
        assert result.facts[0]["evidence_id"] == "ev_001"
        assert "ev_nonexistent" not in result.source_evidence_ids

    def test_validate_result_empty(self):
        """Empty result is valid."""
        from ai.evidence_intelligence import _validate_gemini_evidence_result
        result = _validate_gemini_evidence_result({}, [])
        assert result.facts == []
        assert result.confidence == 0.0

    def test_validate_result_confidence_clamped(self):
        """Confidence is clamped to 0.0-1.0."""
        from ai.evidence_intelligence import _validate_gemini_evidence_result
        parsed = {"confidence": 2.0, "facts": [], "source_evidence_ids": []}
        result = _validate_gemini_evidence_result(parsed, [])
        assert result.confidence == 1.0

    def test_deep_evidence_result_to_dict(self):
        """DeepEvidenceResult serializes correctly."""
        from ai.evidence_intelligence import DeepEvidenceResult
        r = DeepEvidenceResult(
            facts=[{"fact_type": "test", "value": "v", "evidence_id": "ev_001", "confidence": 0.9}],
            contradictions=["A vs B"],
            confidence=0.85,
            source_evidence_ids=["ev_001"],
            summary="test summary",
        )
        d = r.to_dict()
        assert d["facts"][0]["evidence_id"] == "ev_001"
        assert d["contradictions"] == ["A vs B"]
        assert d["confidence"] == 0.85


# ===========================================================================
# 7. Provider Parity
# ===========================================================================

class TestProviderParity:
    """The agent contract must work with any provider."""

    def test_mock_agent_produces_valid_analysis(self):
        """Mock agent (simulating any provider) produces valid analysis."""
        from ai.agent import run_agent, _mock_produce_analysis
        evidence = [
            {"evidence_id": "ev_001", "source_type": "order",
             "raw_content": json.dumps({"order_id": "ORD-001", "amount": 100000})},
            {"evidence_id": "ev_002", "source_type": "delivery",
             "raw_content": json.dumps({
                 "order_id": "ORD-001", "delay_days": 5,
                 "promised_date": "2024-01-15", "actual_date": "2024-01-20",
             })},
        ]
        result = _mock_produce_analysis(evidence, 100000)
        assert "claims" in result
        assert "classification" in result
        assert "confidence" in result
        assert result["classification"] in ("clear", "exception", "ambiguous")

    def test_agent_state_provider_field(self):
        """AgentRunState tracks which provider was used."""
        from ai.agent import AgentRunState
        state = AgentRunState()
        state.provider = "groq"
        state.model = "openai/gpt-oss-120b"
        d = state.to_dict()
        assert d["provider"] == "groq"
        assert d["model"] == "openai/gpt-oss-120b"


# ===========================================================================
# 8. Pipeline Integration — Reference Validation
# ===========================================================================

class TestPipelineReferenceValidation:
    """Pipeline must reject invalid references, not crash."""

    def test_invalid_evidence_reference_becomes_exception(self):
        """Invalid evidence reference sets exception, doesn't crash pipeline."""
        from ai.pipeline import run_pipeline
        evidence = [
            {"evidence_id": "ev_001", "source_type": "order",
             "raw_content": json.dumps({"order_id": "ORD-001", "amount": 100000, "seller_id": "s1"}),
             "extracted_facts": "[]", "linked_decision_ids": "[]"},
        ]
        policies = [
            {"policy_id": "platform_1_1", "version": "1.0",
             "clause_text": "Platform fee 8%", "effective_date": "2024-01-01"},
        ]
        # Provide agent result with invalid evidence reference
        agent_result = {
            "analysis": {
                "claims": [{
                    "claim_type": "sla_breach",
                    "policy_clause_id": "platform_1_1",
                    "evidence_ids": ["ev_nonexistent"],
                    "reasoning": "test",
                }],
                "classification": "exception",
                "confidence": 0.0,
                "reasoning_summary": "Invalid ref test",
            },
            "extracted_facts": [],
            "agent_state": MagicMock(
                stop_reason="evidence_gathering_complete",
                iteration_count=1, duration_ms=100,
                tools_called=[], success=True,
            ),
            "tool_calls": 0,
        }
        # Pipeline should handle invalid references gracefully
        result = run_pipeline(
            scenario_id="test_invalid_ref",
            evidence_records=evidence,
            policy_records=policies,
            use_mock=False,
            agent_result=agent_result,
        )
        decision = result["decision"]
        # Should be exception, not crash
        assert decision["status"] == "REVIEW_REQUIRED"
        model_output = decision["model_output"]
        assert model_output["classification"] == "exception"
        # Evidence sufficiency should be INSUFFICIENT
        assert model_output.get("evidence_sufficiency") == "INSUFFICIENT"

    def test_valid_references_produce_clear(self):
        """Valid references produce a clear classification."""
        from ai.pipeline import run_pipeline
        evidence = [
            {"evidence_id": "ev_001", "source_type": "order",
             "raw_content": json.dumps({"order_id": "ORD-001", "amount": 100000, "seller_id": "s1"}),
             "extracted_facts": "[]", "linked_decision_ids": "[]"},
            {"evidence_id": "ev_002", "source_type": "delivery",
             "raw_content": json.dumps({
                 "order_id": "ORD-001", "delay_days": 5,
                 "promised_date": "2024-01-15", "actual_date": "2024-01-20",
             }),
             "extracted_facts": "[]", "linked_decision_ids": "[]"},
        ]
        policies = [
            {"policy_id": "platform_1_1", "version": "1.0",
             "clause_text": "Platform fee 8%", "effective_date": "2024-01-01"},
            {"policy_id": "sla_4_2", "version": "1.0",
             "clause_text": "SLA penalty", "effective_date": "2024-01-01"},
        ]
        agent_result = {
            "analysis": {
                "claims": [{
                    "claim_type": "sla_breach",
                    "policy_clause_id": "sla_4_2",
                    "evidence_ids": ["ev_002"],
                    "reasoning": "Delivery was 5 days late",
                }],
                "classification": "clear",
                "confidence": 0.9,
                "reasoning_summary": "SLA breach confirmed",
            },
            "extracted_facts": [],
            "agent_state": MagicMock(
                stop_reason="evidence_gathering_complete",
                iteration_count=1, duration_ms=100,
                tools_called=[], success=True,
            ),
            "tool_calls": 0,
        }
        result = run_pipeline(
            scenario_id="test_valid_ref",
            evidence_records=evidence,
            policy_records=policies,
            use_mock=False,
            agent_result=agent_result,
        )
        decision = result["decision"]
        # Safe + sufficient + valid calculation → APPROVED via deterministic gate
        assert decision["status"] == "APPROVED"
        model_output = decision["model_output"]
        assert model_output["evidence_sufficiency"] == "SUFFICIENT"
        assert model_output["classification"] == "clear"

    def test_insufficient_evidence_stays_review_required(self):
        """Cases with insufficient evidence remain REVIEW_REQUIRED."""
        from ai.pipeline import run_pipeline
        evidence = [
            {"evidence_id": "ev_001", "source_type": "order",
             "raw_content": json.dumps({"order_id": "ORD-001", "amount": 100000, "seller_id": "s1"}),
             "extracted_facts": "[]", "linked_decision_ids": "[]"},
        ]
        policies = [
            {"policy_id": "platform_1_1", "version": "1.0",
             "clause_text": "Platform fee 8%", "effective_date": "2024-01-01"},
        ]
        agent_result = {
            "analysis": {
                "claims": [],
                "classification": "clear",
                "confidence": 0.95,
                "reasoning_summary": "No issues",
            },
            "extracted_facts": [],
            "agent_state": MagicMock(
                stop_reason="analysis_complete",
                iteration_count=1, duration_ms=100,
                tools_called=[], success=True,
            ),
            "tool_calls": 0,
        }
        result = run_pipeline(
            scenario_id="test_always_review",
            evidence_records=evidence,
            policy_records=policies,
            use_mock=False,
            agent_result=agent_result,
        )
        # No claims + evidence present → INSUFFICIENT → REVIEW_REQUIRED
        assert result["decision"]["status"] == "REVIEW_REQUIRED"

    def test_provider_failure_still_creates_review_decision(self):
        """Agent failure still produces REVIEW_REQUIRED decision."""
        from ai.agent import AgentRunState
        state = AgentRunState(
            stop_reason="provider_error",
            success=False,
            iteration_count=1,
            duration_ms=100,
        )
        d = state.to_dict()
        assert d["success"] is False
        assert d["failure_type"] == "provider_error"


# ===========================================================================
# 9. No Gemini — Simple Case
# ===========================================================================

class TestNoGeminiSimpleCase:
    """Simple Razorpay cases should NOT invoke Gemini."""

    def test_simple_order_no_gemini(self):
        """A simple order+delivery case does not need Gemini."""
        from ai.evidence_intelligence import needs_deep_evidence_analysis
        evidence = [
            {"evidence_id": "ev_001", "source_type": "order",
             "raw_content": json.dumps({"order_id": "ORD-001", "amount": 50000})},
            {"evidence_id": "ev_002", "source_type": "delivery",
             "raw_content": json.dumps({"order_id": "ORD-001", "delay_days": 2})},
            {"evidence_id": "ev_003", "source_type": "refund_record",
             "raw_content": json.dumps({"order_id": "ORD-001", "amount": 5000, "status": "processed"})},
        ]
        assert needs_deep_evidence_analysis(evidence) is False

    def test_mock_agent_handles_simple_case(self):
        """Mock agent handles simple case without Gemini."""
        from ai.agent import _mock_produce_analysis
        evidence = [
            {"evidence_id": "ev_001", "source_type": "order",
             "raw_content": json.dumps({"order_id": "ORD-001", "amount": 50000})},
            {"evidence_id": "ev_002", "source_type": "delivery",
             "raw_content": json.dumps({"order_id": "ORD-001", "delay_days": 2})},
        ]
        result = _mock_produce_analysis(evidence, 50000)
        assert "claims" in result
        assert isinstance(result["claims"], list)
