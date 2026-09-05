"""Structured exception taxonomy for the reconciliation domain.

Every finance exception carries:
  - code: machine-readable exception code (never a generic string)
  - explanation: human-readable description
  - involved_record_ids: records implicated in the exception
  - financial_impact: calculable money impact in paise (0 when not applicable)
  - evidence_refs: evidence/raw references
  - human_action_required: whether a human must act

The taxonomy is used by the decision gate, batch service, benchmark,
and the finance control room.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


class ExceptionCode(str, enum.Enum):
    MISSING_PAYMENT = "MISSING_PAYMENT"
    MISSING_SETTLEMENT = "MISSING_SETTLEMENT"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    REFUND_MISMATCH = "REFUND_MISMATCH"
    FEE_MISMATCH = "FEE_MISMATCH"
    TAX_MISMATCH = "TAX_MISMATCH"
    DUPLICATE_PAYMENT = "DUPLICATE_PAYMENT"
    DUPLICATE_SETTLEMENT = "DUPLICATE_SETTLEMENT"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    PARTIAL_SETTLEMENT = "PARTIAL_SETTLEMENT"
    LATE_SETTLEMENT = "LATE_SETTLEMENT"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    INVALID_RECORD = "INVALID_RECORD"
    UNRESOLVED_RECONCILIATION = "UNRESOLVED_RECONCILIATION"
    # Tier 5-7 deterministic escalation codes (context-driven review).
    DISPUTE_OPEN = "DISPUTE_OPEN"
    PARTIAL_PAYMENT = "PARTIAL_PAYMENT"
    OVERPAYMENT = "OVERPAYMENT"
    UNLINKED_SETTLEMENT = "UNLINKED_SETTLEMENT"
    # First-class invalid-financial-evidence codes.  These are deterministic
    # outcomes for source data that violates a financial invariant (never
    # AI-repaired, never silently absorbed).
    INVALID_REFUND_TOTAL = "INVALID_REFUND_TOTAL"
    INVALID_REFUND_AMOUNT = "INVALID_REFUND_AMOUNT"
    INVALID_CAPTURED_AMOUNT = "INVALID_CAPTURED_AMOUNT"
    INVALID_FEE_AMOUNT = "INVALID_FEE_AMOUNT"
    INVALID_TAX_AMOUNT = "INVALID_TAX_AMOUNT"
    INVALID_SETTLEMENT_AMOUNT = "INVALID_SETTLEMENT_AMOUNT"
    INVALID_FINANCIAL_RECORD = "INVALID_FINANCIAL_RECORD"
    CALCULATION_ERROR = "CALCULATION_ERROR"
    CONTRADICTORY_FINANCIAL_EVIDENCE = "CONTRADICTORY_FINANCIAL_EVIDENCE"


ALL_EXCEPTION_CODES = {code.value for code in ExceptionCode}


@dataclass
class ReconciliationException:
    """A single structured finance exception."""

    code: str
    explanation: str
    involved_record_ids: list[str] = field(default_factory=list)
    financial_impact: int = 0
    evidence_refs: list[str] = field(default_factory=list)
    human_action_required: bool = True

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "explanation": self.explanation,
            "involved_record_ids": list(self.involved_record_ids),
            "financial_impact": self.financial_impact,
            "evidence_refs": list(self.evidence_refs),
            "human_action_required": self.human_action_required,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ReconciliationException":
        return cls(
            code=data.get("code", ExceptionCode.UNRESOLVED_RECONCILIATION.value),
            explanation=data.get("explanation", ""),
            involved_record_ids=data.get("involved_record_ids", []),
            financial_impact=data.get("financial_impact", 0),
            evidence_refs=data.get("evidence_refs", []),
            human_action_required=data.get("human_action_required", True),
        )


# ---------------------------------------------------------------------------
# Helpers to build canonical exceptions
# ---------------------------------------------------------------------------

def missing_settlement(
    payment_id: str,
    involved_records: list[str],
    evidence_refs: list[str],
    financial_impact: int = 0,
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.MISSING_SETTLEMENT.value,
        explanation=(
            f"No settlement record found for payment {payment_id}. "
            "The merchant has not received a settlement for this capture."
        ),
        involved_record_ids=involved_records,
        financial_impact=financial_impact,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def amount_mismatch(
    payment_id: str,
    expected: int,
    actual: int,
    variance: int,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.AMOUNT_MISMATCH.value,
        explanation=(
            f"Expected settlement {expected} paise differs from actual "
            f"settlement {actual} paise (variance {variance:+d} paise) "
            f"for payment {payment_id}."
        ),
        involved_record_ids=involved_records,
        financial_impact=variance,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def fee_mismatch(
    payment_id: str,
    expected_fee: int,
    actual_fee: int,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.FEE_MISMATCH.value,
        explanation=(
            f"Fee mismatch for payment {payment_id}: expected {expected_fee} "
            f"paise, recorded {actual_fee} paise."
        ),
        involved_record_ids=involved_records,
        financial_impact=actual_fee - expected_fee,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def tax_mismatch(
    payment_id: str,
    expected_tax: int,
    actual_tax: int,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.TAX_MISMATCH.value,
        explanation=(
            f"Tax mismatch for payment {payment_id}: expected {expected_tax} "
            f"paise, recorded {actual_tax} paise."
        ),
        involved_record_ids=involved_records,
        financial_impact=actual_tax - expected_tax,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def refund_mismatch(
    payment_id: str,
    expected_refunds: int,
    recorded_refunds: int,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.REFUND_MISMATCH.value,
        explanation=(
            f"Refund mismatch for payment {payment_id}: expected {expected_refunds} "
            f"paise refunded, records show {recorded_refunds} paise."
        ),
        involved_record_ids=involved_records,
        financial_impact=recorded_refunds - expected_refunds,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def missing_payment(
    external_ref: str,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.MISSING_PAYMENT.value,
        explanation=(
            f"Reconciliation records reference payment {external_ref} but no "
            "payment record exists. Cannot verify capture."
        ),
        involved_record_ids=involved_records,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def duplicate_payment(
    payment_id: str,
    duplicate_record_ids: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.DUPLICATE_PAYMENT.value,
        explanation=(
            f"Duplicate payment records found for {payment_id}: "
            f"{', '.join(duplicate_record_ids)}. Only one capture is authoritative."
        ),
        involved_record_ids=duplicate_record_ids,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def duplicate_settlement(
    payment_id: str,
    duplicate_record_ids: list[str],
    evidence_refs: list[str],
    financial_impact: int = 0,
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.DUPLICATE_SETTLEMENT.value,
        explanation=(
            f"Duplicate settlement records found for payment {payment_id}: "
            f"{', '.join(duplicate_record_ids)}. "
            "The merchant may have been settled twice."
        ),
        involved_record_ids=duplicate_record_ids,
        financial_impact=financial_impact,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def contradictory_evidence(
    payment_id: str,
    involved_records: list[str],
    evidence_refs: list[str],
    detail: str = "",
) -> ReconciliationException:
    fallback = "records disagree on authoritative financial state"
    return ReconciliationException(
        code=ExceptionCode.CONTRADICTORY_EVIDENCE.value,
        explanation=(
            "Contradictory evidence for payment "
            f"{payment_id}: {detail or fallback}."
        ),
        involved_record_ids=involved_records,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def partial_settlement(
    payment_id: str,
    expected: int,
    actual: int,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.PARTIAL_SETTLEMENT.value,
        explanation=(
            f"Settlement for payment {payment_id} is partial: expected {expected} "
            f"paise but only {actual} paise settled."
        ),
        involved_record_ids=involved_records,
        financial_impact=expected - actual,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def late_settlement(
    payment_id: str,
    settled_at: str,
    expected_by: str,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.LATE_SETTLEMENT.value,
        explanation=(
            f"Settlement for payment {payment_id} arrived late: settled "
            f"{settled_at}, expected by {expected_by}."
        ),
        involved_record_ids=involved_records,
        evidence_refs=evidence_refs,
        human_action_required=False,
    )


def ai_unavailable(
    payment_id: str,
    technical_reason: str,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.AI_UNAVAILABLE.value,
        explanation=(
            f"AI interpretation unavailable for payment {payment_id}: "
            f"{technical_reason}. Deterministic evidence was insufficient, so "
            "the case is escalated for human review instead of being guessed."
        ),
        involved_record_ids=involved_records,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def invalid_record(
    record_id: str,
    detail: str,
    evidence_refs: list[str],
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.INVALID_RECORD.value,
        explanation=f"Invalid reconciliation record {record_id}: {detail}",
        involved_record_ids=[record_id],
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def invalid_refund_total(
    payment_id: str,
    captured: int,
    refund_total: int,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    """Refunds exceed the captured amount — impossible source data.

    total_refunds <= captured_amount is a hard financial invariant.  The
    exception preserves the exact recorded values and explains the
    violation deterministically.  AI must never "repair" these amounts.
    """
    return ReconciliationException(
        code=ExceptionCode.INVALID_REFUND_TOTAL.value,
        explanation=(
            f"Invalid refund total for payment {payment_id}: refund records "
            f"total {refund_total} paise but captured amount is only {captured} "
            "paise. Refunds cannot exceed the captured amount; the source "
            "evidence is invalid and requires correction at the source."
        ),
        involved_record_ids=involved_records,
        financial_impact=captured - refund_total,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def invalid_amount(
    code: str,
    payment_id: str,
    field_name: str,
    value: int,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    """A recorded financial amount violates a per-field invariant."""
    return ReconciliationException(
        code=code,
        explanation=(
            f"Invalid {field_name} for payment {payment_id}: recorded value "
            f"{value} paise is not a valid non-negative amount. The source "
            "evidence is invalid and requires correction at the source."
        ),
        involved_record_ids=involved_records,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def calculation_error(
    payment_id: str,
    detail: str,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    """A deterministic calculation could not complete on the supplied evidence."""
    return ReconciliationException(
        code=ExceptionCode.CALCULATION_ERROR.value,
        explanation=(
            f"Deterministic financial calculation failed for payment "
            f"{payment_id}: {detail}"
        ),
        involved_record_ids=involved_records,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def contradictory_financial_evidence(
    payment_id: str,
    detail: str,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    """Source evidence is internally contradictory and cannot be reconciled."""
    return ReconciliationException(
        code=ExceptionCode.CONTRADICTORY_FINANCIAL_EVIDENCE.value,
        explanation=(
            f"Contradictory financial evidence for payment {payment_id}: {detail}"
        ),
        involved_record_ids=involved_records,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def unresolved_reconciliation(
    payment_id: str,
    detail: str,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    return ReconciliationException(
        code=ExceptionCode.UNRESOLVED_RECONCILIATION.value,
        explanation=(
            f"Reconciliation for payment {payment_id} could not be resolved "
            f"deterministically: {detail}"
        ),
        involved_record_ids=involved_records,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def open_dispute(
    payment_id: str,
    involved_records: list[str],
    evidence_refs: list[str],
    status: str = "created",
) -> ReconciliationException:
    """Tier 5 — an open dispute is attached to the payment's case.

    Risk/context only: the dispute does NOT change settlement arithmetic.
    When financial evidence otherwise reconciles, an open dispute escalates
    the case to REVIEW so a human can judge the chargeback risk.
    """
    return ReconciliationException(
        code=ExceptionCode.DISPUTE_OPEN.value,
        explanation=(
            f"Payment {payment_id} has an open dispute (status '{status}'). "
            "The dispute is recorded as risk context and does not alter "
            "the deterministic settlement calculation, but the case requires "
            "human review before it is treated as fully resolved."
        ),
        involved_record_ids=involved_records,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def partial_payment(
    payment_id: str,
    expected: int,
    actual: int,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    """Tier 6 — invoice/payment-link obligation paid only partially."""
    return ReconciliationException(
        code=ExceptionCode.PARTIAL_PAYMENT.value,
        explanation=(
            f"Obligation for payment {payment_id} is only partially paid: "
            f"expected {expected} paise, received {actual} paise across the "
            "related payments. Review required."
        ),
        involved_record_ids=involved_records,
        financial_impact=expected - actual,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def overpayment(
    payment_id: str,
    expected: int,
    actual: int,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    """Tier 6 — payments received exceed the invoiced/linked obligation."""
    return ReconciliationException(
        code=ExceptionCode.OVERPAYMENT.value,
        explanation=(
            f"Payments totalling {actual} paise exceed the linked obligation "
            f"of {expected} paise for payment {payment_id}. Review required."
        ),
        involved_record_ids=involved_records,
        financial_impact=actual - expected,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )


def unlinked_settlement(
    settlement_id: str,
    involved_records: list[str],
    evidence_refs: list[str],
) -> ReconciliationException:
    """Tier 3 — a settlement cannot be deterministically linked to a payment.

    The settlement is preserved as evidence and routed to review — a
    relationship is never guessed.
    """
    return ReconciliationException(
        code=ExceptionCode.UNLINKED_SETTLEMENT.value,
        explanation=(
            f"Settlement {settlement_id} cannot be deterministically linked "
            "to any payment through supported identifiers (payment_id/order_id/"
            "settlement recon). The settlement is preserved as evidence and "
            "must be mapped manually."
        ),
        involved_record_ids=involved_records,
        evidence_refs=evidence_refs,
        human_action_required=True,
    )