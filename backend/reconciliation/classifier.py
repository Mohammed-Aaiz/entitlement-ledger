"""Strict deterministic decision gate for reconciliation.

Top-level outcomes:
    MATCHED           — deterministic evidence fully reconciles
    REVIEW_REQUIRED   — evidence is missing/uncertain; a human must look
    EXCEPTION         — a genuine financial discrepancy requiring action

The gate is intentionally conservative: false auto-resolution is worse
than a review.  AI output NEVER changes the classification computed here;
it is recorded as interpretation only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .calculator import (
    SettlementCalculation,
    FinancialValidationError,
)
from .exceptions import (
    ReconciliationException,
    ExceptionCode,
    amount_mismatch,
    missing_settlement,
    fee_mismatch,
    tax_mismatch,
    refund_mismatch,
    missing_payment,
    duplicate_payment,
    duplicate_settlement,
    contradictory_evidence,
    partial_settlement,
    late_settlement,
    ai_unavailable,
)
from .matcher import MatchResult
from .models import (
    CLASS_MATCHED,
    CLASS_REVIEW_REQUIRED,
    CLASS_EXCEPTION,
    AI_NOT_NEEDED,
    AI_AVAILABLE,
    AI_UNAVAILABLE,
    AI_FAILED,
)

logger = logging.getLogger(__name__)

# Severity ordering: higher wins when multiple exceptions apply
_SEVERITY = {
    CLASS_EXCEPTION: 2,
    CLASS_REVIEW_REQUIRED: 1,
    CLASS_MATCHED: 0,
}


@dataclass
class GateResult:
    classification: str
    exception_codes: list[str]
    exceptions: list[dict]
    explanation: str
    ai_status: str
    ai_confidence: Optional[float]
    ai_technical_reason: str
    calculation: Optional[SettlementCalculation]

    def to_dict(self) -> dict:
        return {
            "classification": self.classification,
            "exception_codes": list(self.exception_codes),
            "exceptions": list(self.exceptions),
            "explanation": self.explanation,
            "ai_status": self.ai_status,
            "ai_confidence": self.ai_confidence,
            "ai_technical_reason": self.ai_technical_reason,
            "calculation": self.calculation.to_dict() if self.calculation else None,
        }


def _merge(
    gate: dict,
    classification: str,
    exceptions: list[ReconciliationException],
    explanation_parts: list[str],
) -> dict:
    """Merge a sub-decision into the accumulated gate result (max severity wins)."""
    if _SEVERITY[classification] > _SEVERITY[gate["classification"]]:
        gate["classification"] = classification
    gate["exceptions"].extend(exceptions)
    gate["exception_codes"].extend(e.code for e in exceptions)
    if explanation_parts:
        gate["explanation_parts"].extend(explanation_parts)
    return gate


def decide(
    payment_id: str,
    match: MatchResult,
    calculation: SettlementCalculation,
    settlement_due_within_days: int = 7,
    payment_recorded_at: Optional[str] = None,
    ai_status: str = AI_NOT_NEEDED,
    ai_confidence: Optional[float] = None,
    ai_technical_reason: str = "",
    force_review: bool = False,
) -> GateResult:
    """Run the deterministic decision gate over matched records.

    Args:
        match: deterministic match result for the payment.
        calculation: deterministic settlement calculation (may already have
            actual settlement attached via compare_settlement).
        settlement_due_within_days: max allowed days between capture and
            settlement before LATE_SETTLEMENT applies.
        payment_recorded_at: capture timestamp (ISO) for lateness checks.
        ai_status / ai_confidence / ai_technical_reason: AI interpretation
            metadata recorded for audit — never used to change money.
        force_review: force REVIEW_REQUIRED even when everything matches
            (used when deterministic evidence alone is insufficient and AI
            is unavailable).

    Returns:
        GateResult with classification, structured exceptions, explanation,
        and the deterministic calculation.
    """
    gate: dict = {
        "classification": CLASS_MATCHED,
        "exceptions": [],
        "exception_codes": [],
        "explanation_parts": [],
    }
    records = []
    if match.payment:
        records.append(match.payment)
    if match.settlement:
        records.append(match.settlement)
    records += match.refunds + match.fee_taxes + match.adjustments

    evidence_refs = sorted({r.raw_evidence_ref for r in records if r.raw_evidence_ref})
    record_ids = [r.record_id for r in records]

    # ── 1. Duplicate detection (highest priority — poisons the books) ──
    if match.payment_duplicates:
        _merge(
            gate, CLASS_EXCEPTION,
            [duplicate_payment(
                match.payment.external_id,
                [d.record_id for d in match.payment_duplicates],
                evidence_refs,
            )],
            ["Duplicate payment records detected."],
        )

    if match.settlement_duplicates:
        financial_impact = sum(d.amount for d in match.settlement_duplicates)
        _merge(
            gate, CLASS_EXCEPTION,
            [duplicate_settlement(
                match.payment.external_id if match.payment else match.settlement.payment_id,
                [d.record_id for d in match.settlement_duplicates],
                evidence_refs,
                financial_impact=financial_impact,
            )],
            ["Duplicate settlement records detected."],
        )

    # ── 2. Missing payment ──
    if not match.payment:
        _merge(
            gate, CLASS_EXCEPTION,
            [missing_payment(
                match.settlement.payment_id if match.settlement else payment_id,
                record_ids,
                evidence_refs,
            )],
            ["No payment record found."],
        )
        # A settlement without a payment cannot be reconciled further.
        return _finalize(gate, calculation, ai_status, ai_confidence, ai_technical_reason)

    payment_id = match.payment.external_id

    # ── 3. Missing settlement ──
    if not match.settlement:
        financial_impact = calculation.expected_settlement
        _merge(
            gate, CLASS_REVIEW_REQUIRED,
            [missing_settlement(payment_id, record_ids, evidence_refs, financial_impact)],
            ["Settlement record missing — expected settlement not yet received."],
        )
        # No actual settlement to compare; force review unless deterministic
        # evidence (a proven zero-amount capture, e.g. refunded-in-full) proves
        # no settlement is due.  Conservative: always REVIEW.
        return _finalize(gate, calculation, ai_status, ai_confidence, ai_technical_reason)

    # ── 4. Fee / tax / refund mismatches (recorded vs. expectation) ──
    recorded_fee = sum(ft.fee_amount for ft in match.fee_taxes)
    recorded_tax = sum(ft.tax_amount for ft in match.fee_taxes)
    # The expected fee/tax is what the deterministic engine used in the
    # calculation trace.  The trace's fee_total/tax_total are the recorded
    # values; mismatches arise when recorded fees/taxes contradict the
    # captured-amount-derived expectation (e.g. a fee larger than captured).
    captured = match.payment.amount
    refund_total = sum(r.amount for r in match.refunds)

    if recorded_fee > captured:
        _merge(
            gate, CLASS_EXCEPTION,
            [fee_mismatch(payment_id, captured, recorded_fee, record_ids, evidence_refs)],
            ["Recorded fee exceeds captured amount."],
        )
    if recorded_tax > captured:
        _merge(
            gate, CLASS_EXCEPTION,
            [tax_mismatch(payment_id, captured, recorded_tax, record_ids, evidence_refs)],
            ["Recorded tax exceeds captured amount."],
        )
    if refund_total > captured:
        _merge(
            gate, CLASS_EXCEPTION,
            [refund_mismatch(payment_id, captured, refund_total, record_ids, evidence_refs)],
            ["Refund total exceeds captured amount."],
        )

    # Contradictory fee/tax records: multiple fee_tax records with
    # different amounts for the same payment are a genuine contradiction.
    fee_vals = {(ft.fee_amount, ft.tax_amount) for ft in match.fee_taxes}
    if len(fee_vals) > 1:
        distinct_fees = {ft.fee_amount for ft in match.fee_taxes}
        distinct_taxes = {ft.tax_amount for ft in match.fee_taxes}
        if len(distinct_fees) > 1:
            _merge(
                gate, CLASS_EXCEPTION,
                [fee_mismatch(
                    payment_id,
                    min(distinct_fees), max(distinct_fees),
                    record_ids, evidence_refs,
                )],
                ["Multiple fee records disagree on the fee amount."],
            )
        if len(distinct_taxes) > 1:
            _merge(
                gate, CLASS_EXCEPTION,
                [tax_mismatch(
                    payment_id,
                    min(distinct_taxes), max(distinct_taxes),
                    record_ids, evidence_refs,
                )],
                ["Multiple fee records disagree on the tax amount."],
            )

    # ── 5. Contradictory evidence ──
    # Deterministic sources of contradiction:
    #   - matcher notes that a total "exceeds" a bound (e.g. refunds > capture)
    #   - matcher notes that the same refund id was recorded with CONFLICTING
    #     amounts across deliveries (each amount individually valid)
    if match.notes:
        contradictory_notes = [
            n for n in match.notes
            if "exceeds" in n or "Contradictory refund" in n
        ]
        if contradictory_notes:
            _merge(
                gate, CLASS_EXCEPTION,
                [contradictory_evidence(
                    payment_id, record_ids, evidence_refs,
                    detail="; ".join(contradictory_notes),
                )],
                ["Contradictory amounts across related records."],
            )

    # ── 6. Actual settlement comparison ──
    # Semantics (sign-preserving):
    #   variance < 0  → actual < expected → genuine PARTIAL_SETTLEMENT
    #   variance > 0  → actual > expected → OVER-settlement (amount mismatch,
    #                   never mislabeled as "partial")
    #   variance == 0 → books reconcile
    # The amount-mismatch exception always carries the signed variance.
    if calculation.actual_settlement is not None:
        variance = calculation.variance or 0
        if variance != 0:
            if variance < 0 and abs(variance) < abs(calculation.expected_settlement):
                _merge(
                    gate, CLASS_REVIEW_REQUIRED,
                    [partial_settlement(
                        payment_id,
                        calculation.expected_settlement,
                        calculation.actual_settlement,
                        record_ids,
                        evidence_refs,
                    )],
                    ["Actual settlement is partial (below expected)."],
                )
            _merge(
                gate, CLASS_EXCEPTION,
                [amount_mismatch(
                    payment_id,
                    calculation.expected_settlement,
                    calculation.actual_settlement,
                    variance,
                    record_ids,
                    evidence_refs,
                )],
                [
                    "Actual settlement is above expected (over-settlement)."
                    if variance > 0 else
                    "Actual settlement differs from expected settlement."
                ],
            )
    else:
        # No actual settlement was provided by the caller — cannot verify.
        _merge(
            gate, CLASS_REVIEW_REQUIRED,
            [missing_settlement(payment_id, record_ids, evidence_refs)],
            ["No actual settlement amount available for comparison."],
        )

    # ── 7. Late settlement ──
    if match.settlement and payment_recorded_at and match.settlement.recorded_at:
        try:
            from datetime import datetime
            captured_dt = datetime.fromisoformat(payment_recorded_at.replace("Z", "+00:00"))
            settled_dt = datetime.fromisoformat(match.settlement.recorded_at.replace("Z", "+00:00"))
            days = (settled_dt - captured_dt).total_seconds() / 86400
            if days > settlement_due_within_days:
                _merge(
                    gate, CLASS_REVIEW_REQUIRED,
                    [late_settlement(
                        payment_id,
                        match.settlement.recorded_at,
                        f"within {settlement_due_within_days} days of capture",
                        record_ids,
                        evidence_refs,
                    )],
                    ["Settlement arrived late."],
                )
        except (ValueError, TypeError):
            logger.warning("Could not parse timestamps for lateness check on %s", payment_id)

    # ── 8. AI unavailable + insufficient deterministic evidence ──
    if force_review and gate["classification"] == CLASS_MATCHED:
        _merge(
            gate, CLASS_REVIEW_REQUIRED,
            [ai_unavailable(payment_id, ai_technical_reason, record_ids, evidence_refs)],
            ["AI interpretation unavailable and deterministic evidence was insufficient."],
        )

    # ── 9. Nothing unresolved → MATCHED ──
    return _finalize(gate, calculation, ai_status, ai_confidence, ai_technical_reason)


def _finalize(
    gate: dict,
    calculation: SettlementCalculation,
    ai_status: str,
    ai_confidence: Optional[float],
    ai_technical_reason: str,
) -> GateResult:
    """Build the final GateResult, deduplicating exception codes."""
    seen_codes: set[str] = set()
    unique_exceptions: list[dict] = []
    unique_codes: list[str] = []
    for exc in gate["exceptions"]:
        if exc.code not in seen_codes:
            seen_codes.add(exc.code)
            unique_exceptions.append(exc.to_dict())
            unique_codes.append(exc.code)

    explanation = " ".join(dict.fromkeys(gate["explanation_parts"]))
    if not explanation:
        if gate["classification"] == CLASS_MATCHED:
            explanation = (
                f"All records reconcile deterministically: expected settlement "
                f"{calculation.expected_settlement} paise matches actual "
                f"{calculation.actual_settlement} paise (variance 0)."
            )
        else:
            explanation = "Review required — see structured exceptions."

    return GateResult(
        classification=gate["classification"],
        exception_codes=unique_codes,
        exceptions=unique_exceptions,
        explanation=explanation,
        ai_status=ai_status,
        ai_confidence=ai_confidence,
        ai_technical_reason=ai_technical_reason,
        calculation=calculation,
    )