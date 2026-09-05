"""Tests for the controlled AI investigation layer.

Covers:
- Deterministic AI gating: clear cases never invoke the provider; genuinely
  ambiguous cases do (demand-driven AI usage).
- force_ai bypass (failure-safety benchmarks): provider invoked for every
  case that reaches the AI block.
- Safety: AI failure on an ambiguous case never produces MATCHED; AI output
  cannot change the deterministic classification or amounts.
- Structured findings: root-cause candidates + evidence ids are schema-valid.
- Razorpay: test-mode detection from key prefix (test credentials are never
  reported as LIVE MODE) and controlled upstream error mapping (401/429/503
  never surface as generic 500s).
- Razorpay event registry: canonical family classification.
"""
from __future__ import annotations

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from reconciliation.models import (
    FinancialRecord,
    CLASS_MATCHED,
    CLASS_REVIEW_REQUIRED,
    CLASS_EXCEPTION,
    AI_NOT_NEEDED,
    AI_AVAILABLE,
    AI_UNAVAILABLE,
    AI_NOT_ATTEMPTED,
)


# ---------------------------------------------------------------------------
# Native-tool-capable test provider (bounded investigation loop)
# ---------------------------------------------------------------------------

class ToolScriptProvider:
    """Provider with native tool calling whose turns are scripted by the test.

    Each entry is a function receiving (messages, tools) and returning a
    ToolCallResponse, letting tests script tool calls then a final answer.
    """

    def __init__(self, turns):
        self.turns = list(turns)
        self.call_count = 0
        self.script = turns

    def provider_info(self):
        return {"provider": "tool_stub", "model": "tool-test"}

    def complete_json(self, *a, **k):
        raise AssertionError("tool-capable provider must use complete_with_tools")

    def complete_with_tools(self, messages, tools, tool_choice="auto",
                            max_tokens=2048, temperature=0.0):
        from ai.llm_provider import ToolCallResponse
        self.call_count += 1
        if not self.turns:
            return ToolCallResponse(content="", tool_calls=[], finish_reason="stop")
        turn = self.turns.pop(0)
        if callable(turn):
            return turn(messages, tools)
        return turn  # pre-built ToolCallResponse

    def is_available(self):
        return True


def _tool_call(tc_id, name, args):
    from ai.llm_provider import ToolCallInfo
    return ToolCallInfo(id=tc_id, function_name=name, arguments=args)


def _grounded_reasoning() -> str:
    """Evidence-grounded reasoning (>= 15 words) for stub interpretations.

    The AI output contract requires user-facing reasoning that is grounded
    in the supplied evidence and never alters monetary values.
    """
    return (
        "The captured payment, recorded fees and the observed settlement amount "
        "do not fully reconcile because the settlement is below the deterministic "
        "expectation, so human review remains appropriate."
    )


def _final_answer(text=None):
    from ai.llm_provider import ToolCallResponse
    if text is None:
        text = json.dumps({
            "evidence_summary": "Investigation complete: partial settlement observed.",
            "identified_relations": [],
            "discrepancy_explanation": "Actual settlement is below expected; likely settlement timing.",
            "contradictions": [],
            "ambiguous": True,
            "confidence": 0.8,
            "suggested_human_review": True,
            "evidence_ids": ["razorpay_settlements:set_2"],
            "related_record_ids": ["pay_2"],
            "root_cause_candidates": [
                {"cause": "PARTIAL_SETTLEMENT", "confidence": 0.7, "reasoning": "Actual below expected."}
            ],
            "reasoning": _grounded_reasoning(),
        })
    return ToolCallResponse(content=text, tool_calls=[], finish_reason="stop")


def _tc_call(name, args):
    return lambda messages, tools: (
        __import__("ai.llm_provider", fromlist=["ToolCallResponse"]).ToolCallResponse(
            content="", tool_calls=[_tool_call(f"call_{name}", name, args)], finish_reason="tool_calls"
        )
    )


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class CountingProvider:
    """Injectable provider that counts invocations and returns valid output.

    Distinguishes "provider genuinely invoked" (call_count > 0) from
    "provider never invoked" (call_count == 0).
    """

    def __init__(self, interpretation=None, fail_with=None):
        self.call_count = 0
        self.interpretation = interpretation or {
            "evidence_summary": "All records present and internally consistent.",
            "identified_relations": [],
            "discrepancy_explanation": "Partial settlement observed; root cause likely settlement timing.",
            "contradictions": [],
            "ambiguous": True,
            "confidence": 0.8,
            "suggested_human_review": True,
            "evidence_ids": ["razorpay_payments:pay_1"],
            "root_cause_candidates": [
                {"cause": "partial_settlement", "confidence": 0.75, "reasoning": "Actual settlement below expected."}
            ],
            "reasoning": _grounded_reasoning(),
        }
        self.fail_with = fail_with

    def provider_info(self):
        return {"provider": "counting_stub", "model": "test-stub"}

    def complete_json(self, prompt, system="", max_tokens=2048, temperature=0.0, response_schema=None):
        self.call_count += 1
        if self.fail_with is not None:
            raise self.fail_with
        return self.interpretation


def _rec(**kw) -> FinancialRecord:
    base = {
        "record_type": "payment",
        "external_id": "pay_1",
        "amount": 100000,
        "payment_id": "pay_1",
        "status": "captured",
        "recorded_at": "2026-01-01T10:00:00Z",
        "raw_evidence_ref": "razorpay_payments:pay_1",
    }
    base.update(kw)
    return FinancialRecord.from_dict(base)


def _exact_match_records() -> list[FinancialRecord]:
    """Payment 100000 − fee 8000 = expected 92000, settlement exactly 92000."""
    return [
        _rec(record_type="payment", external_id="pay_1", amount=100000, payment_id="pay_1"),
        _rec(record_type="fee_tax", external_id="fee_1", amount=0, payment_id="pay_1",
             fee_amount=8000, tax_amount=0, raw_evidence_ref="ev_fee"),
        _rec(record_type="settlement", external_id="set_1", amount=92000, payment_id="pay_1",
             recorded_at="2026-01-02T10:00:00Z", raw_evidence_ref="razorpay_settlements:set_1"),
    ]


def _amount_mismatch_records() -> list[FinancialRecord]:
    """Expected 92000 but actual settlement is 90000 → AMOUNT_MISMATCH / PARTIAL_SETTLEMENT."""
    return [
        _rec(record_type="payment", external_id="pay_2", amount=100000, payment_id="pay_2"),
        _rec(record_type="fee_tax", external_id="fee_2", amount=0, payment_id="pay_2",
             fee_amount=8000, tax_amount=0, raw_evidence_ref="ev_fee"),
        _rec(record_type="settlement", external_id="set_2", amount=90000, payment_id="pay_2",
             recorded_at="2026-01-02T10:00:00Z", raw_evidence_ref="razorpay_settlements:set_2"),
    ]


def _contradictory_records() -> list[FinancialRecord]:
    """Two fee records disagree on the fee amount → contradictory evidence."""
    return [
        _rec(record_type="payment", external_id="pay_3", amount=100000, payment_id="pay_3"),
        _rec(record_type="fee_tax", external_id="fee_3a", amount=0, payment_id="pay_3",
             fee_amount=8000, tax_amount=0, raw_evidence_ref="ev_fee_a"),
        _rec(record_type="fee_tax", external_id="fee_3b", amount=0, payment_id="pay_3",
             fee_amount=12000, tax_amount=0, raw_evidence_ref="ev_fee_b"),
        _rec(record_type="settlement", external_id="set_3", amount=92000, payment_id="pay_3",
             recorded_at="2026-01-02T10:00:00Z", raw_evidence_ref="razorpay_settlements:set_3"),
    ]


def _missing_payment_records() -> list[FinancialRecord]:
    """Settlement only, no capture → MISSING_PAYMENT early return."""
    return [
        _rec(record_type="settlement", external_id="set_4", amount=92000, payment_id="pay_4",
             recorded_at="2026-01-02T10:00:00Z", raw_evidence_ref="razorpay_settlements:set_4"),
    ]


# ---------------------------------------------------------------------------
# Deterministic AI gate
# ---------------------------------------------------------------------------

class TestAIGating:
    """Clear deterministic cases stay AI-free; ambiguous cases invoke AI."""

    def test_exact_match_never_invokes_provider(self):
        """use_ai=True on an exact match → provider NEVER called."""
        from reconciliation.service import reconcile_payment
        provider = CountingProvider()
        case = reconcile_payment("tenant_1", "pay_1", _exact_match_records(),
                                 use_ai=True, provider=provider)
        assert provider.call_count == 0, "provider must not be invoked on an exact match"
        assert case.ai_status == AI_NOT_NEEDED
        assert case.ai_invoked is False
        assert case.classification == CLASS_MATCHED
        assert "deterministic" in case.ai_trigger_reason

    def test_amount_mismatch_invokes_provider(self):
        """Genuine discrepancy (AMOUNT_MISMATCH) → provider invoked."""
        from reconciliation.service import reconcile_payment
        provider = CountingProvider()
        case = reconcile_payment("tenant_1", "pay_2", _amount_mismatch_records(),
                                 use_ai=True, provider=provider)
        assert provider.call_count >= 1, "provider must be invoked for an ambiguous discrepancy"
        assert case.ai_status == AI_AVAILABLE
        assert case.ai_invoked is True
        assert case.classification == CLASS_EXCEPTION
        # AI interpretation is advisory — the classification stays deterministic.
        assert case.ai_interpretation.get("root_cause_candidates")

    def test_contradictory_evidence_invokes_provider(self):
        from reconciliation.service import reconcile_payment
        provider = CountingProvider()
        case = reconcile_payment("tenant_1", "pay_3", _contradictory_records(),
                                 use_ai=True, provider=provider)
        assert provider.call_count >= 1
        assert case.ai_invoked is True
        assert case.classification == CLASS_EXCEPTION

    def test_missing_payment_never_invokes_provider(self):
        """MISSING_PAYMENT exits before the AI block — even with force_ai."""
        from reconciliation.service import reconcile_payment
        provider = CountingProvider()
        case = reconcile_payment("tenant_1", "pay_4", _missing_payment_records(),
                                 use_ai=True, force_ai=True, provider=provider)
        assert provider.call_count == 0
        assert case.ai_status == AI_NOT_ATTEMPTED
        assert "MISSING_PAYMENT" in case.exception_codes
        assert case.classification == CLASS_EXCEPTION

    def test_force_ai_bypasses_gate(self):
        """force_ai=True invokes the provider even on an exact match (failure-safety mode)."""
        from reconciliation.service import reconcile_payment
        provider = CountingProvider()
        case = reconcile_payment("tenant_1", "pay_1", _exact_match_records(),
                                 use_ai=True, force_ai=True, provider=provider)
        assert provider.call_count == 1
        assert case.ai_invoked is True
        assert case.classification == CLASS_MATCHED  # success still deterministic

    def test_deterministic_only_never_invokes_provider(self):
        from reconciliation.service import reconcile_payment
        provider = CountingProvider()
        case = reconcile_payment("tenant_1", "pay_2", _amount_mismatch_records(),
                                 use_ai=False, provider=provider)
        assert provider.call_count == 0
        assert case.classification == CLASS_EXCEPTION

    def test_gate_pure_function(self):
        from reconciliation.ai_controller import should_investigate
        invoke, reason = should_investigate([], variance=0, has_payment=True)
        assert invoke is False
        assert "exact match" in reason

        invoke, _ = should_investigate(["AMOUNT_MISMATCH"], variance=-2000)
        assert invoke is True

        invoke, _ = should_investigate(["CONTRADICTORY_EVIDENCE"])
        assert invoke is True

        invoke, _ = should_investigate(["MISSING_SETTLEMENT"])
        assert invoke is False

        invoke, _ = should_investigate([], capture_conflict=True)
        assert invoke is True

        invoke, _ = should_investigate([], has_payment=False)
        assert invoke is False


# ---------------------------------------------------------------------------
# Safety: AI can never approve, change amounts, or fabricate success
# ---------------------------------------------------------------------------

class TestAISafety:
    def test_ai_failure_on_ambiguous_case_never_matched(self):
        """Provider failure on a gated ambiguous case → safe outcome."""
        from reconciliation.service import reconcile_payment
        provider = CountingProvider(fail_with=ValueError("HTTP 503: provider unavailable"))
        case = reconcile_payment("tenant_1", "pay_2", _amount_mismatch_records(),
                                 use_ai=True, provider=provider)
        assert provider.call_count == 1
        assert case.ai_status == AI_UNAVAILABLE
        assert case.classification != CLASS_MATCHED
        assert "503" in case.ai_technical_reason

    def test_ai_cannot_change_deterministic_amounts(self):
        """AI interpretation never alters expected/actual/variance."""
        from reconciliation.service import reconcile_payment
        lying = CountingProvider(interpretation={
            "evidence_summary": "I think the settlement should be different.",
            "identified_relations": [],
            "discrepancy_explanation": "Trust me, the amount is wrong.",
            "contradictions": [],
            "ambiguous": False,
            "confidence": 0.99,
            "suggested_human_review": False,
            "evidence_ids": [],
            "root_cause_candidates": [],
            # Valid output under the contract: evidence-grounded reasoning.
            "reasoning": (
                "The deterministic engine calculated the expected settlement from "
                "the captured amount, refunds, fees and taxes, and the actual "
                "settlement recorded for this payment differs from that amount."
            ),
        })
        case = reconcile_payment("tenant_1", "pay_2", _amount_mismatch_records(),
                                 use_ai=True, provider=lying)
        # Deterministic money is authoritative: 100000 − 8000 = 92000 expected.
        assert case.expected_amount == 92000
        assert case.actual_amount == 90000
        assert case.variance == -2000
        assert case.classification == CLASS_EXCEPTION  # AI confidence cannot override

    def test_malformed_ai_output_safe(self):
        """Malformed provider output → AI_FAILED, never fabricated success."""
        from reconciliation.service import reconcile_payment

        class MalformedProvider(CountingProvider):
            def complete_json(self, *a, **k):
                self.call_count += 1
                return "this is not valid json {{{"

        provider = MalformedProvider()
        case = reconcile_payment("tenant_1", "pay_2", _amount_mismatch_records(),
                                 use_ai=True, provider=provider)
        assert provider.call_count == 1
        assert case.ai_status == "failed"
        assert "malformed" in case.ai_technical_reason
        assert case.classification != CLASS_MATCHED

    def test_ai_cannot_approve_clean_case_that_needs_review(self):
        """AI saying 'approve everything' never changes a review outcome."""
        from reconciliation.service import reconcile_payment
        # Partial settlement: actual 91000 vs expected 92000.
        records = [
            _rec(record_type="payment", external_id="pay_5", amount=100000, payment_id="pay_5"),
            _rec(record_type="fee_tax", external_id="fee_5", amount=0, payment_id="pay_5",
                 fee_amount=8000, tax_amount=0, raw_evidence_ref="ev_fee"),
            _rec(record_type="settlement", external_id="set_5", amount=91000, payment_id="pay_5",
                 recorded_at="2026-01-02T10:00:00Z", raw_evidence_ref="razorpay_settlements:set_5"),
        ]
        approving_ai = CountingProvider(interpretation={
            "evidence_summary": "All good.",
            "identified_relations": [],
            "discrepancy_explanation": "No issue.",
            "contradictions": [],
            "ambiguous": False,
            "confidence": 1.0,
            "suggested_human_review": False,
            "evidence_ids": [],
            "root_cause_candidates": [],
            # Valid output under the contract: evidence-grounded reasoning.
            "reasoning": (
                "The payment was captured for the full amount and the settlement "
                "recorded for this payment is below the deterministic expected "
                "net amount after fees, leaving a residual variance to review."
            ),
        })
        case = reconcile_payment("tenant_1", "pay_5", records, use_ai=True, provider=approving_ai)
        # Deterministic gate: variance −1000 → PARTIAL_SETTLEMENT → never MATCHED
        assert case.variance == -1000
        assert case.classification != CLASS_MATCHED


# ---------------------------------------------------------------------------
# Razorpay test-mode detection + upstream error mapping
# ---------------------------------------------------------------------------

class TestRazorpayModeDetection:
    def test_test_key_reports_test_mode(self):
        from razorpay_client import get_status
        with patch.dict(os.environ, {
            "RAZORPAY_KEY_ID": "rzp_test_abcd1234",
            "RAZORPAY_KEY_SECRET": "secret",
        }, clear=False):
            status = get_status()
            assert status["configured"] is True
            assert status["mode"] == "test"
            assert status["test_mode"] is True
            assert status["mode"] != "live"  # test credentials are never LIVE

    def test_live_key_reports_live_mode(self):
        from razorpay_client import get_status
        with patch.dict(os.environ, {
            "RAZORPAY_KEY_ID": "rzp_live_abcd1234",
            "RAZORPAY_KEY_SECRET": "secret",
        }, clear=False):
            status = get_status()
            assert status["mode"] == "live"
            assert status["test_mode"] is False

    def test_unconfigured_reports_demo(self):
        from razorpay_client import get_status
        with patch.dict(os.environ, {}, clear=True):
            status = get_status()
            assert status["configured"] is False
            assert status["mode"] == "demo"

    def test_connection_info_mode_from_prefix(self):
        from razorpay_client import get_connection_info
        with patch.dict(os.environ, {
            "RAZORPAY_KEY_ID": "rzp_test_xyz", "RAZORPAY_KEY_SECRET": "s",
        }, clear=False):
            assert get_connection_info()["mode"] == "test"


class TestRazorpayUpstreamErrors:
    """Upstream 401/403/429/5xx map to controlled errors — never generic 500s."""

    def _mock_client_status(self, status_code: int):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = {"error": {"description": "upstream"}}
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.request.return_value = resp
        return client

    def _request_with_status(self, status_code: int):
        from razorpay_client import _request
        with patch.dict(os.environ, {
            "RAZORPAY_KEY_ID": "rzp_test_abc", "RAZORPAY_KEY_SECRET": "s",
        }, clear=False):
            with patch("httpx.Client", return_value=self._mock_client_status(status_code)):
                return _request("GET", "/payments/pay_x")

    def test_401_maps_to_auth_error(self):
        from razorpay_client import RazorpayAPIError
        with pytest.raises(RazorpayAPIError) as exc:
            self._request_with_status(401)
        assert exc.value.category == "auth"
        assert "secret" not in str(exc.value).lower()

    def test_403_maps_to_auth_error(self):
        from razorpay_client import RazorpayAPIError
        with pytest.raises(RazorpayAPIError) as exc:
            self._request_with_status(403)
        assert exc.value.category == "auth"

    def test_429_maps_to_rate_limited(self):
        from razorpay_client import RazorpayAPIError
        with pytest.raises(RazorpayAPIError) as exc:
            self._request_with_status(429)
        assert exc.value.category == "rate_limited"
        assert exc.value.status_code == 429

    def test_503_maps_to_unavailable(self):
        from razorpay_client import RazorpayAPIError
        with pytest.raises(RazorpayAPIError) as exc:
            self._request_with_status(503)
        assert exc.value.category == "unavailable"

    def test_500_maps_to_bad_gateway(self):
        from razorpay_client import RazorpayAPIError
        with pytest.raises(RazorpayAPIError) as exc:
            self._request_with_status(500)
        assert exc.value.category == "bad_gateway"

    def test_missing_credentials_controlled(self):
        from razorpay_client import _request, RazorpayAPIError
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RazorpayAPIError) as exc:
                _request("GET", "/payments/pay_x")
        assert exc.value.category == "auth"


# ---------------------------------------------------------------------------
# Bounded AI investigator — read-only tool safety
# ---------------------------------------------------------------------------

AMOUNT_MISMATCH_CTX = None


def _mismatch_context() -> dict:
    records = []
    for r in _amount_mismatch_records():
        records.append({
            "record_type": r.record_type, "external_id": r.external_id,
            "record_id": r.record_id, "amount": r.amount, "status": r.status,
            "payment_id": r.payment_id, "order_id": r.order_id,
            "fee_amount": r.fee_amount, "tax_amount": r.tax_amount,
            "adjustment_sign": r.adjustment_sign, "recorded_at": r.recorded_at,
            "source": r.source, "raw_evidence_ref": r.raw_evidence_ref,
        })
    return {
        "payment_id": "pay_2",
        "records": records,
        "deterministic_expected_settlement": 92000,
        "deterministic_actual_settlement": 90000,
        "deterministic_variance": -2000,
        "capture_conflict": False,
    }


class TestInvestigatorToolSafety:
    """Read-only tools: validation, tenant rejection, missing records, limits."""

    def test_valid_tool_call_then_finding(self):
        """Investigator executes a valid read-only tool call and returns a finding."""
        from reconciliation.ai_controller import investigate_case
        provider = ToolScriptProvider([
            _tc_call("get_payment", {"payment_id": "pay_2"}),
            _final_answer(),
        ])
        result = investigate_case(_mismatch_context(), provider=provider)
        assert result.status == AI_AVAILABLE
        assert result.tool_call_count == 1
        assert "get_payment" in result.tools_called
        assert result.interpretation["root_cause_candidates"][0]["cause"] == "PARTIAL_SETTLEMENT"

    def test_tool_result_contains_real_record(self):
        """The executed tool returns the actual in-context record (never fabricated)."""
        from reconciliation.ai_controller import _execute_tool, _validate_tool_args
        ctx = _mismatch_context()
        args, err = _validate_tool_args("get_payment", {"payment_id": "pay_2"})
        assert err == ""
        result = _execute_tool("get_payment", args, ctx)
        assert result["found"] is True
        assert result["records"][0]["external_id"] == "pay_2"
        assert result["records"][0]["amount"] == 100000  # real deterministic fact

    def test_invalid_id_rejected(self):
        """Empty/non-string ids are rejected with an explicit error."""
        from reconciliation.ai_controller import _validate_tool_args
        args, err = _validate_tool_args("get_payment", {"payment_id": ""})
        assert err != ""
        args, err = _validate_tool_args("get_payment", {"payment_id": 12345})
        assert err != ""
        args, err = _validate_tool_args("get_payment", "not-a-dict")
        assert err != ""
        args, err = _validate_tool_args("get_payment", {})
        assert err != "" and "payment_id" in err

    def test_tenant_arg_rejected(self):
        """Model cannot supply tenant/security parameters — rejected at validation.

        The executor is only reachable AFTER validation in the real loop, so
        the rejection at the validation boundary is the enforcement point.
        """
        from reconciliation.ai_controller import _validate_tool_args
        args, err = _validate_tool_args("get_case_records", {"tenant_id": "tenant_B"})
        assert err != "" and "not permitted" in err
        args, err = _validate_tool_args("get_payment", {"payment_id": "pay_2", "tenant_id": "tenant_B"})
        assert err != "" and "not permitted" in err

    def test_missing_record_returns_not_found(self):
        """Missing records return found:false — never a crash, never invented data."""
        from reconciliation.ai_controller import _validate_tool_args, _execute_tool
        ctx = _mismatch_context()
        args, _ = _validate_tool_args("get_settlement", {"settlement_id": "set_NOPE"})
        result = _execute_tool("get_settlement", args, ctx)
        assert result["found"] is False

    def test_tool_call_limit_enforced(self):
        """A provider that never stops calling tools is hard-bounded to a safe failure."""
        from reconciliation.ai_controller import investigate_case
        from ai.llm_provider import ToolCallResponse, ToolCallInfo

        def never_stop(messages, tools):
            return ToolCallResponse(
                content="",
                tool_calls=[ToolCallInfo(id=f"c{i}", function_name="get_case_records", arguments={}) for i in range(4)],
                finish_reason="tool_calls",
            )

        provider = ToolScriptProvider([never_stop, never_stop, never_stop])
        result = investigate_case(_mismatch_context(), provider=provider)
        assert result.status == "failed"
        assert "limit" in result.technical_reason
        assert result.tool_call_count >= 8

    def test_provider_timeout_safe(self):
        """Investigator timeout → AI_UNAVAILABLE, safe state, never an approval."""
        from reconciliation.ai_controller import investigate_case

        def timeout(messages, tools):
            raise ValueError("Groq request timed out after 90s")

        provider = ToolScriptProvider([timeout])
        result = investigate_case(_mismatch_context(), provider=provider)
        assert result.status == AI_UNAVAILABLE
        assert "timeout" in result.technical_reason

    def test_full_loop_gate_then_investigator(self):
        """End-to-end: ambiguous case → gate → investigator tool call → deterministic decision."""
        from reconciliation.service import reconcile_payment
        provider = ToolScriptProvider([
            _tc_call("get_payment", {"payment_id": "pay_2"}),
            _final_answer(),
        ])
        case = reconcile_payment("tenant_1", "pay_2", _amount_mismatch_records(),
                                 use_ai=True, provider=provider)
        assert provider.call_count >= 1
        assert case.ai_invoked is True
        assert case.ai_tool_calls == 1
        assert case.ai_status == AI_AVAILABLE
        # AI is advisory: the deterministic gate still decides EXCEPTION.
        assert case.classification == CLASS_EXCEPTION
        assert case.expected_amount == 92000

    def test_single_shot_fallback_when_no_native_tools(self):
        """Providers without native tool calling use the single-shot path (0 tool calls)."""
        from reconciliation.ai_controller import investigate_case
        provider = CountingProvider()
        result = investigate_case(_mismatch_context(), provider=provider)
        assert result.status == AI_AVAILABLE
        assert result.tool_call_count == 0
        assert provider.call_count == 1


# ---------------------------------------------------------------------------
# Deterministic validation of AI output (taxonomy + evidence references)
# ---------------------------------------------------------------------------

class TestDeterministicOutputValidation:
    def test_root_cause_normalized_to_taxonomy(self):
        from reconciliation.ai_controller import normalize_root_cause
        assert normalize_root_cause("SETTLEMENT_TIMING") == "SETTLEMENT_TIMING"
        assert normalize_root_cause("settlement_timing") == "SETTLEMENT_TIMING"
        assert normalize_root_cause("settlement timing") == "SETTLEMENT_TIMING"
        assert normalize_root_cause("fee_discrepancy") == "FEE_DISCREPANCY"
        # Arbitrary model categories are NEVER authoritative.
        assert normalize_root_cause("the CFO is angry") == "UNKNOWN"
        assert normalize_root_cause(None) == "UNKNOWN"

    def test_hallucinated_evidence_ids_dropped(self):
        from reconciliation.ai_controller import (
            ReconciliationInterpretation,
            deterministic_validate_interpretation,
        )
        interp = ReconciliationInterpretation(
            evidence_summary="s",
            evidence_ids=["razorpay_settlements:set_2", "ev_MADE_UP_999"],
            root_cause_candidates=[
                {"cause": "party_confetti_shortage", "confidence": 0.9, "reasoning": ""},
                {"cause": "partial_settlement", "confidence": 0.5, "reasoning": ""},
            ],
        )
        valid_refs = _mismatch_context()
        refs = {
            r["raw_evidence_ref"] for r in valid_refs["records"] if r["raw_evidence_ref"]
        } | {r["external_id"] for r in valid_refs["records"]}
        out = deterministic_validate_interpretation(interp, valid_evidence_refs=refs)
        assert out.evidence_ids == ["razorpay_settlements:set_2"]
        causes = [rc.cause for rc in out.root_cause_candidates]
        assert "UNKNOWN" in causes  # arbitrary category collapsed
        assert "PARTIAL_SETTLEMENT" in causes  # relaxed variant normalized
        assert "party_confetti_shortage" not in causes

    def test_schema_has_no_monetary_fields(self):
        """AI output structurally cannot carry monetary truth."""
        from reconciliation.ai_controller import ReconciliationInterpretation
        fields = set(ReconciliationInterpretation.model_fields.keys())
        assert not (fields & {"amount", "expected_settlement", "actual_settlement",
                              "variance", "final_amount", "captured_amount"})

    def test_malformed_final_answer_fails(self):
        """Non-JSON final answer after tool use → AI_FAILED."""
        from reconciliation.ai_controller import investigate_case
        provider = ToolScriptProvider([
            _tc_call("get_payment", {"payment_id": "pay_2"}),
            _final_answer(text="this is not json {{{{"),
        ])
        result = investigate_case(_mismatch_context(), provider=provider)
        assert result.status == "failed"
        assert "malformed" in result.technical_reason


# ---------------------------------------------------------------------------
# Razorpay event registry
# ---------------------------------------------------------------------------

class TestRazorpayEventRegistry:
    def test_payment_captured_financial(self):
        from razorpay_registry import classify_event
        c = classify_event("payment.captured")
        assert c["family"] == "PAYMENT"
        assert c["known"] is True
        assert c["financial_relevance"] is True
        assert c["affects_reconciliation"] is True
        assert c["context_risk_only"] is False

    def test_refund_processed_financial(self):
        from razorpay_registry import classify_event
        c = classify_event("refund.processed")
        assert c["family"] == "REFUND"
        assert c["affects_reconciliation"] is True

    def test_settlement_processed_financial(self):
        from razorpay_registry import classify_event
        c = classify_event("settlement.processed")
        assert c["family"] == "SETTLEMENT"
        assert c["affects_reconciliation"] is True

    def test_downtime_is_context_only(self):
        from razorpay_registry import classify_event
        c = classify_event("payment.downtime.started")
        assert c["family"] == "DOWNTIME"
        assert c["financial_relevance"] is False
        assert c["affects_reconciliation"] is False
        assert c["context_risk_only"] is True

    def test_dispute_is_risk_context(self):
        from razorpay_registry import classify_event
        c = classify_event("payment.dispute.created")
        assert c["family"] == "DISPUTE"
        assert c["context_risk_only"] is True
        assert c["ai_useful"] is True

    def test_unknown_event_conservative(self):
        from razorpay_registry import classify_event
        c = classify_event("totally.new.event")
        assert c["family"] == "UNKNOWN"
        assert c["known"] is False
        assert c["affects_reconciliation"] is False  # never silently financial
        assert c["context_risk_only"] is True

    def test_event_serialization_includes_family(self):
        """Stored events expose canonical family metadata via the serializer."""
        from razorpay_routes import _serialize_event
        event = {
            "event_id": "evt_1", "event_type": "payment.captured",
            "source": "live_webhook", "verification_status": "verified",
            "razorpay_entity_type": "payment", "razorpay_entity_id": "pay_1",
            "payment_id": "pay_1", "order_id": "order_1", "amount": 100000,
            "currency": "INR", "status": "captured", "event_timestamp": "2026-01-01T00:00:00+00:00",
            "received_at": "2026-01-01T00:00:00+00:00",
            "extracted_facts": "[]", "linked_decision_id": None,
        }
        s = _serialize_event(event)
        assert s["event_family"] == "PAYMENT"
        assert s["financial_relevance"] is True
        assert s["known_event"] is True

    def test_store_event_records_family_fact(self):
        """store_event persists the canonical family into extracted facts."""
        import asyncio
        from razorpay_events import store_event
        payload = {
            "id": "evt_family_1", "event": "payment.captured",
            "created_at": 1767225600,
            "payload": {"payment": {"entity": {
                "id": "pay_f1", "amount": 100000, "currency": "INR",
                "status": "captured", "order_id": "order_f1",
            }}},
        }
        event = asyncio.get_event_loop().run_until_complete(
            store_event(payload, source="live_webhook", verification_status="verified",
                        tenant_id="demo")
        )
        facts = json.dumps(event.get("extracted_facts", []))
        assert "PAYMENT" in facts
        assert "financial_relevance=True" in facts

# ---------------------------------------------------------------------------
# Persisted AI metadata: real columns round-trip through the database
# ---------------------------------------------------------------------------

def _ensure_tenant(tenant_id: str) -> None:
    """Insert a tenant row (reconciliation tables FK to tenants)."""
    import asyncio
    import database

    async def _insert():
        db = await database.get_db()
        try:
            await db.execute(
                "INSERT OR IGNORE INTO tenants (tenant_id, name) VALUES (?, ?)",
                (tenant_id, f"test {tenant_id}"),
            )
            await db.commit()
        finally:
            await db.close()

    asyncio.new_event_loop().run_until_complete(_insert())


class TestPersistedAIMetadata:
    """ai_invoked / ai_trigger_reason / ai_tool_calls are REAL persisted
    columns (migration 0007 + database.py DDL) — never a JSON metadata hack."""

    def test_ai_metadata_round_trips_through_db(self):
        import asyncio
        import database
        from reconciliation.dataset import generate_dataset, records_for_inference
        from reconciliation.service import run_batch

        tenant = "persist_tenant"
        # A settlement amount mismatch is a genuinely ambiguous case (AI gate
        # would invoke anyway); force_ai guarantees invocation for this test.
        cases = [c for c in generate_dataset(count=60, seed=7)
                 if c.scenario == "settlement_amount_mismatch"]
        assert cases, "dataset must contain a settlement_amount_mismatch case"
        records = records_for_inference(cases[0])
        _ensure_tenant(tenant)

        provider = CountingProvider()
        run_batch(tenant, records, use_ai=True, force_ai=True,
                  provider=provider, source="persist_test")
        assert provider.call_count >= 1, "force_ai must genuinely invoke the provider"

        async def _fetch():
            db = await database.get_db()
            try:
                cur = await db.execute(
                    "SELECT * FROM reconciliation_cases WHERE tenant_id = ?", (tenant,))
                rows = await cur.fetchall()
                return rows
            finally:
                await db.close()

        rows = asyncio.new_event_loop().run_until_complete(_fetch())
        assert rows, "case must be persisted"
        row = rows[0]

        # Real columns hold real values
        assert int(row["ai_invoked"]) == 1
        assert row["ai_trigger_reason"], "trigger reason must persist"
        assert "force_ai" in row["ai_trigger_reason"]
        assert isinstance(row["ai_tool_calls"], int)
        assert row["ai_tool_calls"] >= 0

        # The stored interpretation JSON carries structured findings — and the
        # legacy "_gate" metadata hack must be gone from storage entirely.
        interp = json.loads(row["ai_interpretation"]) if row["ai_interpretation"] else {}
        assert "_gate" not in interp
        assert "root_cause_candidates" in interp

        # The API response mapping consumes these columns directly.
        from reconciliation.routes import _case_to_response
        resp = _case_to_response(row)
        assert resp["ai_invoked"] is True
        assert resp["ai_trigger_reason"] == row["ai_trigger_reason"]
        assert resp["ai_tool_calls"] == row["ai_tool_calls"]
        assert resp["ai_status"] == AI_AVAILABLE

    def test_deterministic_case_persists_zero_invocation(self):
        """A clean match persists ai_invoked=0 with the gate reason — proving
        the column distinguishes 'not invoked' from 'invoked and failed'."""
        import asyncio
        import database
        from reconciliation.dataset import generate_dataset, records_for_inference
        from reconciliation.service import run_batch

        tenant = "persist_clean"
        # A clean match is the canonical AI-free case: the gate must keep the
        # provider at zero invocations even though use_ai=True.
        cases = [c for c in generate_dataset(count=30, seed=42)
                 if c.scenario == "clean_match"]
        assert cases, "dataset must contain a clean_match case"
        records = records_for_inference(cases[0])
        _ensure_tenant(tenant)

        provider = CountingProvider()
        run_batch(tenant, records, use_ai=True, provider=provider, source="persist_test")
        assert provider.call_count == 0, "clean deterministic batch must not invoke AI"

        async def _fetch():
            db = await database.get_db()
            try:
                cur = await db.execute(
                    "SELECT * FROM reconciliation_cases WHERE tenant_id = ?", (tenant,))
                rows = await cur.fetchall()
                return rows
            finally:
                await db.close()

        rows = asyncio.new_event_loop().run_until_complete(_fetch())
        assert rows
        assert all(int(r["ai_invoked"]) == 0 for r in rows)
        assert all(r["ai_status"] == AI_NOT_NEEDED for r in rows)


class TestInvestigationToolSchema:
    """Optional tool parameters must never be forced into JSON-Schema
    ``required`` — doing so makes Groq reject valid tool calls (e.g. a
    ``search_related_records`` call without ``record_type``) with HTTP 400
    tool_use_failed."""

    def test_optional_params_not_required(self):
        from reconciliation.ai_controller import INVESTIGATION_TOOLS
        by_name = {t["function"]["name"]: t["function"] for t in INVESTIGATION_TOOLS}

        # record_type is optional on both tools: in properties, not required.
        for name in ("search_related_records", "get_case_records"):
            params = by_name[name]["parameters"]
            assert "record_type" in params["properties"]
            assert "record_type" not in params["required"], (
                f"{name}: optional record_type forced into required")

        # required-only params on these tools may stay required.
        assert by_name["get_settlement"]["parameters"]["required"] == ["settlement_id"]
        assert by_name["get_payment"]["parameters"]["required"] == ["payment_id"]
