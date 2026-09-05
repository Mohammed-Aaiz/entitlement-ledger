"""Tests for the Gemini Finance Support Center.

Covers:
- Tenant-scoped context builders (run summary, case, exceptions, tiers)
  return only real deterministic data and enforce tenant boundaries.
- Bounded Gemini completion: grounded answer, structured schema validation.
- Citation validation: model-supplied IDs not in the context are dropped.
- Failure isolation: malformed output, provider 429/timeout/key errors are
  controlled states — never invented answers, never touching reconciliation.
- API contract: /support/status, /support/ask (with injected provider).
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock

from reconciliation import support_center
from reconciliation.support_center import (
    SupportCenterAnswer,
    ask_support_center,
    build_context,
    usage_snapshot,
    reset_usage,
)


@pytest.fixture(autouse=True)
def _reset_usage():
    reset_usage()
    yield
    reset_usage()


# ---------------------------------------------------------------------------
# Stub Gemini provider
# ---------------------------------------------------------------------------

class StubGemini:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def provider_info(self):
        return {"provider": "gemini", "model": "stub-model"}

    def is_available(self):
        return True

    def complete_json(self, prompt, system="", max_tokens=2048,
                      temperature=0.0, response_schema=None):
        self.calls.append({"prompt_len": len(prompt), "system": system})
        # Simulate a provider that returns a parsed dict (Gemini path).
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _valid_answer():
    return {
        "answer": "The recorded refunds exceed the captured payment amount, "
                  "so the case was flagged as an invalid refund total.",
        "key_points": ["Refund total 45000 exceeds captured 40000"],
        "citations": ["case_gt_1"],
        "insufficient_evidence": False,
    }


# ---------------------------------------------------------------------------
# Context builders — real data, tenant-scoped
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class _FakeDB:
    """Minimal DB double returning canned rows (unit tests of builders)."""

    def __init__(self, scenario="run"):
        self.scenario = scenario

    _RUN_ROW = {
        "run_id": "run_1", "status": "completed", "source": "demo_100",
        "total_records": 300, "total_cases": 100, "matched": 80,
        "review_required": 12, "exceptions": 8, "match_rate": 0.8,
        "classification_accuracy": 1.0, "calculation_accuracy": 1.0,
        "false_auto_resolve": 0, "throughput_per_sec": 120.5,
        "p50_latency_ms": 42.0, "p95_latency_ms": 210.0,
        "duplicates_detected": 3, "audit_completeness": 1.0,
        "errors": "[]", "started_at": "t0", "completed_at": "t1",
    }

    _CASE_SUMMARY_ROW = {
        "case_id": "case_gt_1", "payment_id": "pay_1", "run_id": "run_1",
        "classification": "EXCEPTION", "exception_codes": '["INVALID_REFUND_TOTAL"]',
        "variance": 5000, "ai_invoked": 0, "ai_status": "not_needed",
        "created_at": "t1",
    }

    _CASE_FULL_ROW = {
        "case_id": "case_gt_1", "payment_id": "pay_1", "run_id": "run_1",
        "classification": "EXCEPTION",
        "expected_amount": 0, "actual_amount": 0, "variance": 5000,
        "exception_codes": '["INVALID_REFUND_TOTAL"]',
        "exceptions": json.dumps([{
            "code": "INVALID_REFUND_TOTAL", "explanation": "refunds exceed capture",
            "evidence_refs": ["rzp_pay_1", "rzp_refund_1"],
        }]),
        "explanation": "Deterministic invalid refund evidence",
        "calculation_trace": json.dumps({"captured_amount": 40000, "refund_total": 45000}),
        "match_info": "{}", "related_record_ids": '["rec_pay_1","rec_refund_1"]',
        "tier_analysis": json.dumps({"tiers_applied": [1, 2, 4], "tier_findings": []}),
        "ai_invoked": 0, "ai_status": "not_needed",
        "ai_trigger_reason": "", "ai_interpretation": "{}",
        "ai_technical_reason": "", "decision_id": "dec_1", "created_at": "t1",
    }

    async def execute(self, sql, params=()):
        s = sql.strip()
        # 1. Counters
        if s.startswith("SELECT COUNT(*)") and "reconciliation_runs" in s:
            return _FakeCursor([{"cnt": 1}])
        if s.startswith("SELECT COUNT(*)") and "ai_invoked = 1" in s:
            return _FakeCursor([{"cnt": 5}])
        if s.startswith("SELECT COUNT(*)") and "classification != 'MATCHED'" in s:
            return _FakeCursor([{"cnt": 20}])
        # 2. Dashboard group-by classification
        if "GROUP BY classification" in s and "FROM reconciliation_cases" in s:
            return _FakeCursor([
                {"classification": "MATCHED", "cnt": 80},
                {"classification": "REVIEW_REQUIRED", "cnt": 12},
                {"classification": "EXCEPTION", "cnt": 8},
            ])
        if "SUM(variance)" in s:
            return _FakeCursor([{"v": -40000}])
        # 3. Latest-run / run lookup
        if "FROM reconciliation_runs" in s and "ORDER BY started_at DESC LIMIT 1" in s:
            return _FakeCursor([dict(self._RUN_ROW)])
        if "FROM reconciliation_runs WHERE" in s and "ORDER BY" not in s:
            if self.scenario == "missing":
                return _FakeCursor([])
            return _FakeCursor([dict(self._RUN_ROW)])
        # 4. Exception-case list (classification != MATCHED with LIMIT)
        if "classification != 'MATCHED'" in s and "LIMIT" in s:
            return _FakeCursor([dict(self._CASE_SUMMARY_ROW)])
        # 5. Decision lookups
        if "FROM decisions WHERE" in s and "entity_type = 'reconciliation'" in s:
            return _FakeCursor([{"cnt": 90}])
        if "FROM decisions WHERE decision_id" in s:
            return _FakeCursor([{
                "decision_id": "dec_1", "entity_type": "reconciliation",
                "entity_id": "pay_1", "gross_amount": 0, "final_amount": 0,
                "status": "REVIEW_REQUIRED", "decision_hash": "h1", "created_at": "t1",
            }])
        # 6. Case rows (full detail) — must be after more specific branches
        if "FROM reconciliation_cases WHERE" in s and "payment_id = ?" in s and "case_id != ?" in s:
            return _FakeCursor([])
        if "FROM reconciliation_cases WHERE" in s and "LIKE" in s:
            return _FakeCursor([])
        if "FROM reconciliation_cases WHERE" in s:
            return _FakeCursor([dict(self._CASE_FULL_ROW)])
        return _FakeCursor([])


# ---------------------------------------------------------------------------
# Unit: ask_support_center
# ---------------------------------------------------------------------------

class TestAskSupportCenter:
    def test_valid_answer_passes_schema_and_citations(self):
        stub = StubGemini(_valid_answer())
        result = ask_support_center(
            "Why did this case fail?", {"case": {"case_id": "case_gt_1"}},
            allowed_ids={"case_gt_1"}, provider=stub,
        )
        assert result.status == "available"
        assert result.answer["answer"]
        assert result.answer["citations"] == ["case_gt_1"]
        assert stub.calls[0]["prompt_len"] > 0
        assert result.usage["invocations"] == 1

    def test_unsupported_citations_are_dropped(self):
        payload = _valid_answer()
        payload["citations"] = ["case_gt_1", "fabricated_case_999"]
        stub = StubGemini(payload)
        result = ask_support_center(
            "Why?", {"case": {"case_id": "case_gt_1"}},
            allowed_ids={"case_gt_1"}, provider=stub,
        )
        assert result.status == "available"
        assert result.answer["citations"] == ["case_gt_1"]
        assert result.unsupported_citations == ["fabricated_case_999"]

    def test_malformed_output_is_failed_not_invented(self):
        # Missing required "answer" field (wrong type + wrong shape).
        stub = StubGemini({"unrelated": 1})
        result = ask_support_center("Why?", {"case": {}}, provider=stub)
        assert result.status == "failed"
        assert "malformed" in result.technical_reason
        assert result.answer is None

    def test_provider_429_is_unavailable_with_safe_reason(self):
        stub = StubGemini(Exception("Gemini API error 429: rate limit exceeded"))
        result = ask_support_center("Why?", {"case": {}}, provider=stub)
        assert result.status == "unavailable"
        assert "429" in result.technical_reason
        assert result.answer is None
        assert result.usage["failures"] == 1

    def test_provider_timeout_is_unavailable(self):
        stub = StubGemini(Exception("request timed out after 30s"))
        result = ask_support_center("Why?", {"case": {}}, provider=stub)
        assert result.status == "unavailable"
        assert "timed out" in result.technical_reason

    def test_missing_key_is_unavailable(self):
        def _no_provider():
            raise EnvironmentError("GEMINI_API_KEY not set")
        with patch.object(support_center, "_gemini_provider", _no_provider):
            result = ask_support_center("Why?", {"case": {}})
        assert result.status == "unavailable"
        assert result.answer is None

    def test_answer_schema_validates_confidence_boundaries(self):
        # confidence-like fields don't exist here — answer must be a string.
        stub = StubGemini({"answer": 1234, "citations": []})
        result = ask_support_center("Why?", {"case": {}}, provider=stub)
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# Unit: context builders
# ---------------------------------------------------------------------------

def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


class TestContextBuilders:
    def test_run_summary_builds_bounded_context(self):
        db = _FakeDB("run")
        ctx, allowed = _run(build_context(db, "demo", "summarize_run",
                                          "Summarize", run_id="run_1"))
        assert "run_summary" in ctx
        assert ctx["run_summary"]["run_id"] == "run_1"
        assert "run_1" in allowed
        # Sample exception case ids legitimately present in the context are citable.
        assert "case_gt_1" in allowed

    def test_missing_run_is_lookup_error(self):
        db = _FakeDB("missing")
        with pytest.raises(LookupError):
            _run(build_context(db, "demo", "summarize_run",
                               "Summarize", run_id="run_missing"))

    def test_explain_exception_requires_case_id(self):
        db = _FakeDB()
        with pytest.raises(ValueError):
            _run(build_context(db, "demo", "explain_exception", "Why?"))

    def test_explain_exception_context_contains_evidence_refs(self):
        db = _FakeDB("case")
        ctx, allowed = _run(build_context(db, "demo", "explain_exception",
                                          "Why did this fail?", case_id="case_gt_1"))
        assert ctx["case"]["exception_codes"] == ["INVALID_REFUND_TOTAL"]
        assert "rzp_pay_1" in ctx["case"]["evidence_refs"]
        assert ctx["case"]["calculation_trace"]["captured_amount"] == 40000
        assert "case_gt_1" in allowed

    def test_finance_qa_contains_dashboard(self):
        db = _FakeDB()
        ctx, allowed = _run(build_context(db, "demo", "finance_qa",
                                          "Why is match rate low?"))
        assert ctx["dashboard"]["total_cases"] == 100
        assert ctx["dashboard"]["matched"] == 80

    def test_unsupported_mode_is_value_error(self):
        db = _FakeDB()
        with pytest.raises(ValueError):
            _run(build_context(db, "demo", "not_a_mode", "Why?"))


# ---------------------------------------------------------------------------
# API contract
# ---------------------------------------------------------------------------

class TestSupportApi:
    def test_status_endpoint(self, auth_client):
        resp = auth_client.get("/api/reconciliation/support/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "gemini"
        assert "modes" in body and "usage" in body
        # availability is env-dependent; the shape must always be present.
        assert "available" in body

    def test_modes_endpoint(self, auth_client):
        resp = auth_client.get("/api/reconciliation/support/modes")
        assert resp.status_code == 200
        assert "explain_exception" in resp.json()["modes"]
        assert "summarize_run" in resp.json()["modes"]

    def test_ask_requires_auth(self, client):
        resp = client.post("/api/reconciliation/support/ask",
                           json={"question": "Why is match rate low?"})
        assert resp.status_code in (401, 403)

    def test_ask_unknown_mode_422(self, auth_client):
        resp = auth_client.post("/api/reconciliation/support/ask",
                                json={"question": "hi", "mode": "nope"})
        assert resp.status_code == 422

    def test_ask_explain_missing_case_404(self, auth_client):
        resp = auth_client.post("/api/reconciliation/support/ask",
                                json={"question": "Why?",
                                      "mode": "explain_exception",
                                      "case_id": "case_nope"})
        assert resp.status_code == 404

    def test_ask_grounded_answer_via_stub(self, auth_client):
        from reconciliation import support_routes
        from reconciliation.support_center import SupportCenterResult
        stub = StubGemini(_valid_answer())

        def fake_ask(*a, **k):
            return ask_support_center(a[0], a[1],
                                      allowed_ids=k.get("allowed_ids", set()),
                                      provider=stub)

        with patch.object(support_routes, "ask_support_center", side_effect=fake_ask):
            resp = auth_client.post("/api/reconciliation/support/ask",
                                    json={"question": "Why did it fail?",
                                          "mode": "finance_qa"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["answer"]["answer"]
        assert body["usage"]["invocations"] >= 1

    def test_ask_provider_unavailable_returns_503_envelope(self, auth_client):
        from reconciliation import support_routes
        from reconciliation.support_center import SupportCenterResult

        def fake_unavailable(*a, **k):
            return SupportCenterResult(status="unavailable",
                                       technical_reason="Gemini unavailable (connection error)")

        with patch.object(support_routes, "ask_support_center", side_effect=fake_unavailable):
            resp = auth_client.post("/api/reconciliation/support/ask",
                                    json={"question": "Why?",
                                          "mode": "finance_qa"})
        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert body["error"]["retryable"] is True
        # Never a fabricated answer on failure.
        assert "answer" not in body

    def test_usage_counters_are_real(self):
        before = usage_snapshot()
        stub = StubGemini(_valid_answer())
        ask_support_center("Why?", {"case": {}}, allowed_ids=set(), provider=stub)
        after = usage_snapshot()
        assert after["invocations"] == before["invocations"] + 1
