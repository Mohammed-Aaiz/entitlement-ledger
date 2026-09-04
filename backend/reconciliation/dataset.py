"""Deterministic synthetic finance dataset for reconciliation benchmarking.

Contains 100 ground-truth cases covering the realistic finance-ops problem
space: clean matches, partial refunds, fee/tax mismatches, settlement
mismatches, missing settlements, duplicate events/records, missing
payments, partial settlements, contradictory evidence, late settlements,
invalid records, and AI-unavailable scenarios.

Ground truth is HIDDEN from the controller: the service ingests only
``records``; ground truth is used exclusively by the benchmark evaluator.
Generation is fully deterministic from a seed.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .calculator import calculate_expected_settlement
from .models import FinancialRecord, RECORD_PAYMENT, RECORD_REFUND, RECORD_SETTLEMENT, RECORD_FEE_TAX, RECORD_ADJUSTMENT

# ── Scenario ids ────────────────────────────────────────────────────
S_CLEAN_MATCH = "clean_match"
S_PARTIAL_REFUND = "partial_refund"
S_MULTI_REFUNDS = "multiple_partial_refunds"
S_FEE_MISMATCH = "fee_mismatch"
S_TAX_MISMATCH = "tax_mismatch"
S_SETTLEMENT_MISMATCH = "settlement_amount_mismatch"
S_MISSING_SETTLEMENT = "missing_settlement"
S_DUP_WEBHOOK = "duplicate_webhook"
S_DUP_PAYMENT = "duplicate_payment"
S_DUP_SETTLEMENT = "duplicate_settlement"
S_MISSING_PAYMENT = "missing_payment"
S_PARTIAL_SETTLEMENT = "partial_settlement"
S_CONTRADICTORY = "contradictory_evidence"
S_LATE_SETTLEMENT = "late_settlement"
S_INVALID_RECORD = "invalid_record"
S_AI_UNAVAILABLE = "ai_unavailable"

ALL_SCENARIOS = [
    S_CLEAN_MATCH, S_PARTIAL_REFUND, S_MULTI_REFUNDS,
    S_FEE_MISMATCH, S_TAX_MISMATCH, S_SETTLEMENT_MISMATCH,
    S_MISSING_SETTLEMENT, S_DUP_WEBHOOK, S_DUP_PAYMENT,
    S_DUP_SETTLEMENT, S_MISSING_PAYMENT, S_PARTIAL_SETTLEMENT,
    S_CONTRADICTORY, S_LATE_SETTLEMENT, S_INVALID_RECORD,
    S_AI_UNAVAILABLE,
]

# 100-case deterministic distribution
DEFAULT_DISTRIBUTION = {
    S_CLEAN_MATCH: 19,
    S_PARTIAL_REFUND: 10,
    S_MULTI_REFUNDS: 8,
    S_FEE_MISMATCH: 6,
    S_TAX_MISMATCH: 6,
    S_SETTLEMENT_MISMATCH: 8,
    S_MISSING_SETTLEMENT: 8,
    S_DUP_WEBHOOK: 5,
    S_DUP_PAYMENT: 5,
    S_DUP_SETTLEMENT: 4,
    S_MISSING_PAYMENT: 4,
    S_PARTIAL_SETTLEMENT: 5,
    S_CONTRADICTORY: 3,
    S_LATE_SETTLEMENT: 3,
    S_INVALID_RECORD: 4,
    S_AI_UNAVAILABLE: 2,
}

CAPTURED_AMOUNTS = [25000, 40000, 50000, 75000, 80000, 100000, 120000, 150000, 200000, 350000]
FEE = 2400
TAX = 300
ADJUSTMENT = -1000  # e.g. refund-processing fee charged to merchant


@dataclass
class DatasetCase:
    """One ground-truth reconciliation dataset case."""

    case_id: str
    scenario: str
    payment_id: str
    order_id: str
    records: list[FinancialRecord] = field(default_factory=list)
    # Hidden ground truth — NEVER passed to the controller
    ground_truth: dict = field(default_factory=dict)


def _captured(rng: random.Random) -> int:
    return rng.choice(CAPTURED_AMOUNTS)


def _dt(base: datetime, days: int) -> str:
    return (base + timedelta(days=days)).isoformat()


def _payment_record(case_id: str, payment_id: str, order_id: str, amount: int,
                    recorded_at: str, source: str = "fixture", n: int = 0,
                    payload_hash: str = "") -> FinancialRecord:
    return FinancialRecord(
        record_type=RECORD_PAYMENT,
        external_id=payment_id,
        amount=amount,
        payment_id=payment_id,
        order_id=order_id,
        status="captured",
        recorded_at=recorded_at,
        source=source,
        raw_evidence_ref=f"evt_{case_id}_pay{n}",
        payload_hash=payload_hash,
        record_id=f"rec_{case_id}_pay{n}",
    )


def _settlement_record(case_id: str, payment_id: str, amount: int,
                       recorded_at: str, n: int = 0, source: str = "fixture") -> FinancialRecord:
    return FinancialRecord(
        record_type=RECORD_SETTLEMENT,
        external_id=f"set_{case_id}_{n}",
        amount=amount,
        payment_id=payment_id,
        status="processed",
        recorded_at=recorded_at,
        source=source,
        raw_evidence_ref=f"evt_{case_id}_set{n}",
        record_id=f"rec_{case_id}_set{n}",
    )


def _refund_record(case_id: str, payment_id: str, amount: int,
                   recorded_at: str, n: int = 0) -> FinancialRecord:
    return FinancialRecord(
        record_type=RECORD_REFUND,
        external_id=f"ref_{case_id}_{n}",
        amount=amount,
        payment_id=payment_id,
        status="processed",
        recorded_at=recorded_at,
        source="fixture",
        raw_evidence_ref=f"evt_{case_id}_ref{n}",
        record_id=f"rec_{case_id}_ref{n}",
    )


def _fee_tax_record(case_id: str, payment_id: str, fee: int, tax: int,
                    n: int = 0) -> FinancialRecord:
    return FinancialRecord(
        record_type=RECORD_FEE_TAX,
        external_id=f"ft_{case_id}_{n}",
        amount=fee + tax,
        payment_id=payment_id,
        fee_amount=fee,
        tax_amount=tax,
        status="processed",
        source="fixture",
        raw_evidence_ref=f"evt_{case_id}_ft{n}",
        record_id=f"rec_{case_id}_ft{n}",
    )


def _adjustment_record(case_id: str, payment_id: str, amount: int, sign: str) -> FinancialRecord:
    return FinancialRecord(
        record_type=RECORD_ADJUSTMENT,
        external_id=f"adj_{case_id}",
        amount=amount,
        payment_id=payment_id,
        adjustment_sign=sign,
        status="processed",
        source="fixture",
        raw_evidence_ref=f"evt_{case_id}_adj",
        record_id=f"rec_{case_id}_adj",
    )


def _generate_case(case_id: str, scenario: str, rng: random.Random, idx: int) -> DatasetCase:
    """Generate one deterministic dataset case with hidden ground truth."""
    payment_id = f"pay_{case_id}"
    order_id = f"order_{case_id}"
    base = datetime(2025, 1, 1) + timedelta(days=idx)

    captured = _captured(rng)
    expected = calculate_expected_settlement(
        captured_amount=captured, refund_total=0, fee_total=FEE, tax_total=TAX,
        adjustments=0,
    ).expected_settlement

    gt_exception = None
    gt_classification = "MATCHED"
    gt_false_auto_resolve = False
    records: list[FinancialRecord] = []

    def _with_fee_tax():
        """Append the standard fee/tax record for this payment."""
        records.append(_fee_tax_record(case_id, payment_id, FEE, TAX))

    if scenario == S_CLEAN_MATCH:
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        _with_fee_tax()
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 2)))

    elif scenario == S_PARTIAL_REFUND:
        refund = rng.choice([5000, 10000, 15000, 20000])
        expected = calculate_expected_settlement(
            captured, refund_total=refund, fee_total=FEE, tax_total=TAX, adjustments=0,
        ).expected_settlement
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        _with_fee_tax()
        records.append(_refund_record(case_id, payment_id, refund, _dt(base, 1)))
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 3)))

    elif scenario == S_MULTI_REFUNDS:
        parts = rng.sample([3000, 5000, 7000, 10000], 3)
        refund_total = sum(parts)
        expected = calculate_expected_settlement(
            captured, refund_total=refund_total, fee_total=FEE, tax_total=TAX, adjustments=0,
        ).expected_settlement
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        _with_fee_tax()
        for i, amt in enumerate(parts):
            records.append(_refund_record(case_id, payment_id, amt, _dt(base, 1 + i)))
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 4)))

    elif scenario == S_FEE_MISMATCH:
        # Two fee_tax records disagree on the fee → FEE_MISMATCH
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        records.append(_fee_tax_record(case_id, payment_id, FEE, TAX, 0))
        records.append(_fee_tax_record(case_id, payment_id, FEE + 1500, TAX, 1))
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 2)))
        gt_classification = "EXCEPTION"
        gt_exception = "FEE_MISMATCH"
        gt_false_auto_resolve = True

    elif scenario == S_TAX_MISMATCH:
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        records.append(_fee_tax_record(case_id, payment_id, FEE, TAX, 0))
        records.append(_fee_tax_record(case_id, payment_id, FEE, TAX + 200, 1))
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 2)))
        gt_classification = "EXCEPTION"
        gt_exception = "TAX_MISMATCH"
        gt_false_auto_resolve = True

    elif scenario == S_SETTLEMENT_MISMATCH:
        delta = rng.choice([4400, -4400, 1000, -2500])
        actual = expected + delta
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        _with_fee_tax()
        records.append(_settlement_record(case_id, payment_id, actual, _dt(base, 2)))
        gt_classification = "EXCEPTION"
        gt_exception = "AMOUNT_MISMATCH"
        gt_false_auto_resolve = True

    elif scenario == S_MISSING_SETTLEMENT:
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        _with_fee_tax()
        gt_classification = "REVIEW_REQUIRED"
        gt_exception = "MISSING_SETTLEMENT"
        gt_false_auto_resolve = True

    elif scenario == S_DUP_WEBHOOK:
        # Identical payload → idempotent dedup at ingestion; reconciles clean.
        ph = f"hash_{case_id}"
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0),
                                       source="live_webhook", payload_hash=ph))
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0),
                                       source="live_webhook", payload_hash=ph, n=1))
        _with_fee_tax()
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 2)))
        gt_classification = "MATCHED"
        gt_exception = None

    elif scenario == S_DUP_PAYMENT:
        # Same payment_id, DIFFERENT amounts → genuine duplicate capture.
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        records.append(_payment_record(case_id, payment_id, order_id, captured + 10000, _dt(base, 0), n=1))
        _with_fee_tax()
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 2)))
        gt_classification = "EXCEPTION"
        gt_exception = "DUPLICATE_PAYMENT"
        gt_false_auto_resolve = True

    elif scenario == S_DUP_SETTLEMENT:
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        _with_fee_tax()
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 2), 0))
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 2), 1))
        gt_classification = "EXCEPTION"
        gt_exception = "DUPLICATE_SETTLEMENT"
        gt_false_auto_resolve = True

    elif scenario == S_MISSING_PAYMENT:
        # Settlement + refund reference a payment with no payment record.
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 2)))
        records.append(_refund_record(case_id, payment_id, 5000, _dt(base, 1)))
        gt_classification = "EXCEPTION"
        gt_exception = "MISSING_PAYMENT"
        gt_false_auto_resolve = True

    elif scenario == S_PARTIAL_SETTLEMENT:
        shortfall = rng.choice([5000, 10000])
        actual = expected - shortfall
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        _with_fee_tax()
        records.append(_settlement_record(case_id, payment_id, actual, _dt(base, 2)))
        gt_classification = "EXCEPTION"
        gt_exception = "AMOUNT_MISMATCH"
        gt_false_auto_resolve = True

    elif scenario == S_CONTRADICTORY:
        # Refund records exceed the captured amount → contradictory.
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        records.append(_refund_record(case_id, payment_id, captured + 5000, _dt(base, 1)))
        records.append(_settlement_record(case_id, payment_id, 0, _dt(base, 2)))
        gt_classification = "EXCEPTION"
        gt_exception = "REFUND_MISMATCH"
        gt_false_auto_resolve = True

    elif scenario == S_LATE_SETTLEMENT:
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        _with_fee_tax()
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 10)))  # > 7 days
        gt_classification = "REVIEW_REQUIRED"
        gt_exception = "LATE_SETTLEMENT"

    elif scenario == S_INVALID_RECORD:
        # A payment record with a negative amount → rejected at ingestion.
        records.append(FinancialRecord(
            record_type=RECORD_PAYMENT,
            external_id=payment_id,
            amount=-5000,
            payment_id=payment_id,
            order_id=order_id,
            status="captured",
            source="fixture",
            raw_evidence_ref=f"evt_{case_id}_pay0",
            record_id=f"rec_{case_id}_pay0",
        ))
        gt_classification = "EXCEPTION"
        gt_exception = "INVALID_RECORD"
        gt_false_auto_resolve = True

    elif scenario == S_AI_UNAVAILABLE:
        # Deterministic evidence is genuinely ambiguous: the payment's
        # capture state is disputed between payment.authorized and
        # payment.captured events.  Only AI interpretation (or human
        # review) can disambiguate.  Without AI this MUST NOT auto-approve.
        pay = _payment_record(case_id, payment_id, order_id, captured, _dt(base, 0))
        pay.extra = {
            "capture_conflict": True,
            "note": "payment.authorized and payment.captured events disagree on capture state",
        }
        records.append(pay)
        _with_fee_tax()
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 2)))
        gt_classification = "REVIEW_REQUIRED"
        gt_exception = "AI_UNAVAILABLE"
        gt_false_auto_resolve = True

    if scenario in (S_MISSING_PAYMENT, S_INVALID_RECORD):
        # No valid capture exists — the controller cannot compute an
        # expected settlement for these cases.
        expected_amount = 0
    elif scenario == S_CONTRADICTORY:
        # Refunds exceed capture → deterministic expected settlement is
        # undefined (negative); the contradiction IS the outcome.
        expected_amount = 0
    else:
        # Ground truth expected amount = the deterministic expected
        # settlement derived from the RECORDS (same computation the
        # controller performs), including summed fee/tax records.
        fee_total_gt = sum(r.fee_amount for r in records if r.record_type == RECORD_FEE_TAX)
        tax_total_gt = sum(r.tax_amount for r in records if r.record_type == RECORD_FEE_TAX)
        expected_amount = calculate_expected_settlement(
            captured_amount=captured,
            refund_total=sum(r.amount for r in records if r.record_type == RECORD_REFUND),
            fee_total=fee_total_gt,
            tax_total=tax_total_gt,
            adjustments=0,
        ).expected_settlement

    return DatasetCase(
        case_id=case_id,
        scenario=scenario,
        payment_id=payment_id,
        order_id=order_id,
        records=records,
        ground_truth={
            "classification": gt_classification,
            "expected_amount": expected_amount,
            "exception_code": gt_exception,
            "false_auto_resolve_risk": gt_false_auto_resolve,
            "scenario": scenario,
        },
    )


def generate_dataset(count: int = 100, seed: int = 42) -> list[DatasetCase]:
    """Generate a deterministic ground-truth dataset.

    Args:
        count: number of cases (default 100).
        seed: deterministic seed.

    Returns:
        List of DatasetCase with HIDDEN ground truth.
    """
    rng = random.Random(seed)
    cases: list[DatasetCase] = []
    idx = 0
    for scenario in ALL_SCENARIOS:
        n = DEFAULT_DISTRIBUTION.get(scenario, 0)
        for _ in range(n):
            if len(cases) >= count:
                break
            case_id = f"R{idx + 1:03d}"
            cases.append(_generate_case(case_id, scenario, rng, idx))
            idx += 1
        if len(cases) >= count:
            break
    # If distribution undershoots (custom count), pad with clean matches.
    while len(cases) < count:
        case_id = f"R{idx + 1:03d}"
        cases.append(_generate_case(case_id, S_CLEAN_MATCH, rng, idx))
        idx += 1
    return cases


def records_for_inference(case: DatasetCase) -> list[dict]:
    """Return the records WITHOUT ground truth — what the controller sees."""
    return [r.to_dict() for r in case.records]