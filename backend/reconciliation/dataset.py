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
from .models import (
    FinancialRecord,
    RECORD_PAYMENT,
    RECORD_REFUND,
    RECORD_SETTLEMENT,
    RECORD_FEE_TAX,
    RECORD_ADJUSTMENT,
    RECORD_DISPUTE,
    RECORD_INVOICE,
    RECORD_PAYMENT_LINK,
    RECORD_OPERATIONAL,
)

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
S_INVALID_REFUND = "invalid_refund_total"
S_AI_UNAVAILABLE = "ai_unavailable"
# Tier 5-7 context scenarios (financial records are clean; the context
# record drives the expected deterministic outcome through the engine's
# tier escalation rules — ground truth mirrors what the engine produces).
S_DISPUTE_OPEN = "dispute_open"          # Tier 5 — clean + open dispute → review
S_DISPUTE_CLOSED = "dispute_closed"      # Tier 5 — clean + won/lost dispute → matched
S_INVOICE_PARTIAL = "invoice_partial"    # Tier 6 — clean + partial obligation → review
S_INVOICE_OVERPAY = "invoice_overpay"    # Tier 6 — clean + overpayment vs obligation → review
S_PAYMENT_LINK_PAID = "payment_link_paid"  # Tier 6 — clean + link fully paid → matched
S_OPERATIONAL_DOWNTIME = "operational_downtime"  # Tier 7 — clean + downtime context → matched

ALL_SCENARIOS = [
    S_CLEAN_MATCH, S_PARTIAL_REFUND, S_MULTI_REFUNDS,
    S_FEE_MISMATCH, S_TAX_MISMATCH, S_SETTLEMENT_MISMATCH,
    S_MISSING_SETTLEMENT, S_DUP_WEBHOOK, S_DUP_PAYMENT,
    S_DUP_SETTLEMENT, S_MISSING_PAYMENT, S_PARTIAL_SETTLEMENT,
    S_CONTRADICTORY, S_LATE_SETTLEMENT, S_INVALID_RECORD,
    S_INVALID_REFUND, S_AI_UNAVAILABLE,
    S_DISPUTE_OPEN, S_DISPUTE_CLOSED,
    S_INVOICE_PARTIAL, S_INVOICE_OVERPAY, S_PAYMENT_LINK_PAID,
    S_OPERATIONAL_DOWNTIME,
]

# 100-case deterministic distribution (tiers 1-7 across the batch)
DEFAULT_DISTRIBUTION = {
    S_CLEAN_MATCH: 14,
    S_PARTIAL_REFUND: 9,
    S_MULTI_REFUNDS: 7,
    S_FEE_MISMATCH: 5,
    S_TAX_MISMATCH: 5,
    S_SETTLEMENT_MISMATCH: 7,
    S_MISSING_SETTLEMENT: 7,
    S_DUP_WEBHOOK: 4,
    S_DUP_PAYMENT: 4,
    S_DUP_SETTLEMENT: 3,
    S_MISSING_PAYMENT: 3,
    S_PARTIAL_SETTLEMENT: 4,
    S_CONTRADICTORY: 2,
    S_LATE_SETTLEMENT: 3,
    S_INVALID_RECORD: 3,
    S_INVALID_REFUND: 2,
    S_AI_UNAVAILABLE: 2,
    S_DISPUTE_OPEN: 4,
    S_DISPUTE_CLOSED: 2,
    S_INVOICE_PARTIAL: 3,
    S_INVOICE_OVERPAY: 2,
    S_PAYMENT_LINK_PAID: 2,
    S_OPERATIONAL_DOWNTIME: 3,
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


def _dispute_record(case_id: str, payment_id: str, status: str,
                    recorded_at: str) -> FinancialRecord:
    """Tier 5 context record: a dispute attached to a payment.

    Never a money input — the calculator ignores context records.
    """
    return FinancialRecord(
        record_type=RECORD_DISPUTE,
        external_id=f"disp_{case_id}",
        amount=0,
        payment_id=payment_id,
        status=status,
        recorded_at=recorded_at,
        source="fixture",
        raw_evidence_ref=f"evt_{case_id}_disp",
        record_id=f"rec_{case_id}_disp",
        extra={"family": "dispute", "entity_id": f"disp_{case_id}"},
    )


def _invoice_record(case_id: str, payment_id: str, order_id: str, owed: int,
                    status: str, recorded_at: str) -> FinancialRecord:
    """Tier 6 context record: an invoice/payment-link obligation."""
    return FinancialRecord(
        record_type=RECORD_INVOICE,
        external_id=f"inv_{case_id}",
        amount=owed,
        payment_id=payment_id,
        order_id=order_id,
        status=status,
        recorded_at=recorded_at,
        source="fixture",
        raw_evidence_ref=f"evt_{case_id}_inv",
        record_id=f"rec_{case_id}_inv",
        extra={"family": "invoice", "entity_id": f"inv_{case_id}"},
    )


def _payment_link_record(case_id: str, payment_id: str, order_id: str, owed: int,
                         status: str, recorded_at: str) -> FinancialRecord:
    """Tier 6 context record: a payment-link obligation."""
    return FinancialRecord(
        record_type=RECORD_PAYMENT_LINK,
        external_id=f"pl_{case_id}",
        amount=owed,
        payment_id=payment_id,
        order_id=order_id,
        status=status,
        recorded_at=recorded_at,
        source="fixture",
        raw_evidence_ref=f"evt_{case_id}_pl",
        record_id=f"rec_{case_id}_pl",
        extra={"family": "payment_link", "entity_id": f"pl_{case_id}"},
    )


def _operational_record(case_id: str, payment_id: str, family: str,
                        event_type: str, recorded_at: str) -> FinancialRecord:
    """Tier 7 context record: downtime / account / operational context."""
    return FinancialRecord(
        record_type=RECORD_OPERATIONAL,
        external_id=f"op_{case_id}",
        amount=0,
        payment_id=payment_id,
        status="occurred",
        recorded_at=recorded_at,
        source="fixture",
        raw_evidence_ref=f"evt_{case_id}_op",
        record_id=f"rec_{case_id}_op",
        extra={"family": family, "event_type": event_type},
    )


def _clean_financial_records(case_id: str, payment_id: str, order_id: str,
                             captured: int, expected: int,
                             base: datetime, n_settle: int = 2) -> list[FinancialRecord]:
    """Standard clean financial spine: capture + fee/tax + exact settlement."""
    return [
        _payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)),
        _fee_tax_record(case_id, payment_id, FEE, TAX),
        _settlement_record(case_id, payment_id, expected, _dt(base, n_settle)),
    ]


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
            # Each partial refund is a DISTINCT refund (distinct refund id) —
            # never the same refund id with conflicting amounts.
            records.append(_refund_record(case_id, payment_id, amt, _dt(base, 1 + i), n=i + 1))
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
        # Contradictory evidence WITHIN valid financial invariants: the SAME
        # refund id is delivered twice (two distinct webhook payloads) with
        # CONFLICTING amounts (2000 vs 8000 paise).  Each record is
        # individually valid — refund_total (sum) stays <= captured — but the
        # records contradict each other, so this is genuine contradictory
        # refund evidence, NOT invalid source data.
        refund_conf_a = _refund_record(case_id, payment_id, 2000, _dt(base, 1), 0)
        refund_conf_b = _refund_record(case_id, payment_id, 8000, _dt(base, 1), 1)
        # Same refund entity id, two different payloads (must survive ingest
        # dedup because their payload hashes differ).
        refund_conf_a.external_id = f"ref_{case_id}_x"
        refund_conf_b.external_id = f"ref_{case_id}_x"
        refund_conf_a.payload_hash = f"h_{case_id}_conf_a"
        refund_conf_b.payload_hash = f"h_{case_id}_conf_b"
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        records.append(refund_conf_a)
        records.append(refund_conf_b)
        _with_fee_tax()
        expected_with_refunds = calculate_expected_settlement(
            captured, refund_total=10000, fee_total=FEE, tax_total=TAX,
            adjustments=0,
        ).expected_settlement
        records.append(_settlement_record(case_id, payment_id, expected_with_refunds, _dt(base, 2)))
        gt_classification = "EXCEPTION"
        gt_exception = "CONTRADICTORY_EVIDENCE"
        gt_false_auto_resolve = True

    elif scenario == S_INVALID_REFUND:
        # EXPLICIT malformed-source-data scenario: refund records exceed the
        # captured amount (total_refunds > captured_amount), violating the
        # hard financial invariant.  Tagged invalid — never AI-repaired, the
        # deterministic engine must emit INVALID_REFUND_TOTAL + EXCEPTION.
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        records.append(_refund_record(case_id, payment_id, captured + 5000, _dt(base, 1)))
        gt_classification = "EXCEPTION"
        gt_exception = "INVALID_REFUND_TOTAL"
        gt_false_auto_resolve = True

    elif scenario == S_LATE_SETTLEMENT:
        records.append(_payment_record(case_id, payment_id, order_id, captured, _dt(base, 0)))
        _with_fee_tax()
        records.append(_settlement_record(case_id, payment_id, expected, _dt(base, 10)))  # > 7 days
        gt_classification = "REVIEW_REQUIRED"
        gt_exception = "LATE_SETTLEMENT"

    elif scenario == S_INVALID_RECORD:
        # A payment record with a negative amount.  Through the batch/API
        # ingestion path this is rejected as INVALID_RECORD; through the
        # per-case reconcile path used by the benchmark the calculator
        # rejects the negative capture first and emits
        # INVALID_CAPTURED_AMOUNT.  The dataset ground truth reflects the
        # deterministic engine code on the reconcile path (the code the
        # benchmark measures) — classification stays EXCEPTION either way.
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
        gt_exception = "INVALID_CAPTURED_AMOUNT"
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

    elif scenario == S_DISPUTE_OPEN:
        # Tier 5: money reconciles exactly, but an OPEN dispute means the
        # case must be reviewed — never silently MATCHED.
        records.extend(_clean_financial_records(case_id, payment_id, order_id,
                                                captured, expected, base))
        records.append(_dispute_record(case_id, payment_id, "open", _dt(base, 3)))
        gt_classification = "REVIEW_REQUIRED"
        gt_exception = "DISPUTE_OPEN"
        gt_false_auto_resolve = True

    elif scenario == S_DISPUTE_CLOSED:
        # Tier 5: money reconciles exactly and the dispute was resolved
        # (won/lost) — deterministic outcome stands as MATCHED.
        records.extend(_clean_financial_records(case_id, payment_id, order_id,
                                                captured, expected, base))
        records.append(_dispute_record(case_id, payment_id, "won", _dt(base, 3)))
        gt_classification = "MATCHED"
        gt_exception = None

    elif scenario == S_INVOICE_PARTIAL:
        # Tier 6: money reconciles exactly but the invoice obligation is
        # only partially paid → obligation escalation to review.
        owed = int(captured * 1.5)
        records.extend(_clean_financial_records(case_id, payment_id, order_id,
                                                captured, expected, base))
        records.append(_invoice_record(case_id, payment_id, order_id, owed,
                                       "partially_paid", _dt(base, 1)))
        gt_classification = "REVIEW_REQUIRED"
        gt_exception = "PARTIAL_PAYMENT"
        gt_false_auto_resolve = True

    elif scenario == S_INVOICE_OVERPAY:
        # Tier 6: money reconciles exactly but payments exceed the invoice
        # obligation → overpayment escalation to review.
        owed = int(captured * 0.6)
        records.extend(_clean_financial_records(case_id, payment_id, order_id,
                                                captured, expected, base))
        records.append(_invoice_record(case_id, payment_id, order_id, owed,
                                       "paid", _dt(base, 1)))
        gt_classification = "REVIEW_REQUIRED"
        gt_exception = "OVERPAYMENT"
        gt_false_auto_resolve = True

    elif scenario == S_PAYMENT_LINK_PAID:
        # Tier 6: a payment link fully covers the obligation and money
        # reconciles exactly → MATCHED (context finding only).
        records.extend(_clean_financial_records(case_id, payment_id, order_id,
                                                captured, expected, base))
        records.append(_payment_link_record(case_id, payment_id, order_id, captured,
                                            "paid", _dt(base, 1)))
        gt_classification = "MATCHED"
        gt_exception = None

    elif scenario == S_OPERATIONAL_DOWNTIME:
        # Tier 7: money reconciles exactly; a downtime event adds operational
        # context only — never financial truth, never an escalation.
        records.extend(_clean_financial_records(case_id, payment_id, order_id,
                                                captured, expected, base))
        records.append(_operational_record(case_id, payment_id, "downtime",
                                           "payment.downtime.started", _dt(base, 0)))
        gt_classification = "MATCHED"
        gt_exception = None

    if scenario in (S_MISSING_PAYMENT, S_INVALID_RECORD, S_INVALID_REFUND):
        # No valid capture (or no valid capture math) exists — the
        # controller cannot compute an expected settlement for these cases.
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