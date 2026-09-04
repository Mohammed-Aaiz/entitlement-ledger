"""Reconciliation benchmark runner.

Evaluates the finance controller against the 100-record ground-truth
dataset.  Three clearly distinguished modes:

  deterministic : NO AI at all — pure deterministic pipeline.
                  This measures the deterministic engine, not AI.
  llm           : REAL LLM interpretation via the configured provider.
                  This measures real AI-assisted interpretation.
  failure       : SIMULATED provider failures (429/503/timeout/malformed/
                  tool-incompat/missing-key) — proves AI failure never
                  produces an unsafe approval.  NEVER labeled AI accuracy.

Ground truth is HIDDEN from the controller: only records are passed in.
"""
from __future__ import annotations

import json
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from reconciliation.dataset import generate_dataset, records_for_inference, DatasetCase
from reconciliation.service import reconcile_payment
from reconciliation.models import (
    FinancialRecord,
    CLASS_MATCHED,
    CLASS_REVIEW_REQUIRED,
    CLASS_EXCEPTION,
    AI_AVAILABLE,
    AI_UNAVAILABLE,
    AI_FAILED,
)


@dataclass
class ReconciliationCaseResult:
    """Result of evaluating one dataset case against the controller."""

    case_id: str
    scenario: str
    payment_id: str
    classification: str = ""
    expected_amount: int = 0
    actual_amount: int = 0
    variance: int = 0
    exception_codes: list[str] = field(default_factory=list)
    ai_status: str = ""
    ai_technical_reason: str = ""
    latency_ms: int = 0
    decision_id: str = ""
    # Ground truth (used only by the evaluator)
    gt_classification: str = ""
    gt_exception_code: str = ""
    gt_expected_amount: int = 0
    gt_false_auto_resolve_risk: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "scenario": self.scenario,
            "payment_id": self.payment_id,
            "classification": self.classification,
            "expected_amount": self.expected_amount,
            "actual_amount": self.actual_amount,
            "variance": self.variance,
            "exception_codes": list(self.exception_codes),
            "ai_status": self.ai_status,
            "ai_technical_reason": self.ai_technical_reason,
            "latency_ms": self.latency_ms,
            "decision_id": self.decision_id,
            "ground_truth_classification": self.gt_classification,
            "ground_truth_exception_code": self.gt_exception_code,
            "ground_truth_expected_amount": self.gt_expected_amount,
            "ground_truth_false_auto_resolve_risk": self.gt_false_auto_resolve_risk,
            "error": self.error,
        }


@dataclass
class ReconciliationBenchmarkRun:
    run_id: str = field(default_factory=lambda: f"recbench_{uuid.uuid4().hex[:10]}")
    mode: str = "deterministic"
    failure_mode: str = ""
    provider: str = ""
    model: str = ""
    dataset_seed: int = 42
    total_cases: int = 0
    # Instrumentation for failure mode: how many times the injected provider
    # was genuinely invoked (0 in deterministic mode).
    provider_invocations: int = 0
    started_at: str = ""
    completed_at: str = ""
    duration_ms: int = 0
    results: list[ReconciliationCaseResult] = field(default_factory=list)


def run_reconciliation_benchmark(
    count: int = 100,
    seed: int = 42,
    mode: str = "deterministic",
    failure_mode: str = "429",
) -> ReconciliationBenchmarkRun:
    """Run the reconciliation benchmark.

    Args:
        count: number of dataset cases (default 100).
        seed: deterministic dataset seed.
        mode: 'deterministic' | 'llm' | 'failure'.
        failure_mode: for mode='failure', which provider failure to simulate
            (429 | 503 | timeout | malformed | tool_incompat | missing_key).
    """
    run = ReconciliationBenchmarkRun(mode=mode, failure_mode=failure_mode, dataset_seed=seed)
    run.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    dataset = generate_dataset(count=count, seed=seed)
    run.total_cases = len(dataset)

    provider = None
    if mode == "llm":
        from ai.llm_provider import get_provider
        provider = get_provider()
        info = provider.provider_info()
        run.provider = info.get("provider", "")
        run.model = info.get("model", "")
    elif mode == "failure":
        from benchmark.failing_provider import get_failing_provider
        provider = get_failing_provider(failure_mode)
        run.provider = provider.provider_info()["provider"]
        run.model = "failure-stub"

    clock = time.perf_counter
    start = clock()
    # failure mode REQUESTs AI like llm mode — the injected failing provider
    # is genuinely invoked for every case and must fail safely.  deterministic
    # mode keeps NO AI at all.
    use_ai = mode in ("llm", "failure")
    for case in dataset:
        case_start = clock()
        result = _evaluate_case(case, provider, use_ai=use_ai)
        result.latency_ms = max(int((clock() - case_start) * 1000), 1)
        run.results.append(result)

    # Record how many times the provider was actually invoked (failure-stub
    # instrumentation; real providers report 0 here).
    run.provider_invocations = getattr(provider, "call_count", 0) if provider is not None else 0

    # perf_counter-based duration; never report a zero-duration run.
    run.duration_ms = max(int((clock() - start) * 1000), 1)
    run.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return run


def _evaluate_case(case: DatasetCase, provider, use_ai: bool) -> ReconciliationCaseResult:
    """Run one dataset case through the controller and attach ground truth."""
    result = ReconciliationCaseResult(
        case_id=case.case_id,
        scenario=case.scenario,
        payment_id=case.payment_id,
        gt_classification=case.ground_truth["classification"],
        gt_exception_code=case.ground_truth["exception_code"],
        gt_expected_amount=case.ground_truth["expected_amount"],
        gt_false_auto_resolve_risk=case.ground_truth["false_auto_resolve_risk"],
    )

    try:
        records = [FinancialRecord.from_dict(r) for r in records_for_inference(case)]
        reconciled = reconcile_payment(
            tenant_id="benchmark",
            payment_id=case.payment_id,
            records=records,
            use_ai=use_ai,
            provider=provider,
            order_id=case.order_id,
        )
        result.classification = reconciled.classification
        result.expected_amount = reconciled.expected_amount
        result.actual_amount = reconciled.actual_amount
        result.variance = reconciled.variance
        result.exception_codes = reconciled.exception_codes
        result.ai_status = reconciled.ai_status
        result.ai_technical_reason = reconciled.ai_technical_reason
        result.decision_id = reconciled.decision_id
    except Exception as e:  # noqa: BLE001 — benchmark must not abort
        result.error = f"{type(e).__name__}: {e}"
        result.classification = "ERROR"

    return result