"""Deterministic record matching engine.

Relates payments, refunds, settlements, fee/tax and adjustment records
using ONLY provable identifiers — never AI guesswork.  AI-assisted
interpretation is used only after every deterministic strategy is
exhausted (handled by the service, not here).

Match priority:
  1. exact payment_id
  2. exact settlement/payment reference
  3. exact refund/payment relationship
  4. exact order_id
  5. amount/date consistency
  6. duplicate detection
  7. missing record detection
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

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
    CONTEXT_RECORD_TYPES,
)

# Non-payment financial record types the calculator consumes.
_FINANCIAL_RECORD_TYPES = {
    RECORD_REFUND, RECORD_SETTLEMENT, RECORD_FEE_TAX, RECORD_ADJUSTMENT,
}


@dataclass
class MatchResult:
    """Structured outcome of matching records for one payment."""

    matched: bool
    match_type: str  # payment_id | settlement_reference | refund_relationship | order_id | amount_date_consistency | none
    payment: Optional[FinancialRecord] = None
    settlement: Optional[FinancialRecord] = None
    refunds: list[FinancialRecord] = field(default_factory=list)
    fee_taxes: list[FinancialRecord] = field(default_factory=list)
    adjustments: list[FinancialRecord] = field(default_factory=list)
    # Tier 5-7 context records (disputes, invoices, payment links, operational
    # events) linked deterministically by payment_id / order_id.  These are
    # evidence only — they never enter the settlement calculation.
    context_records: list[FinancialRecord] = field(default_factory=list)
    payment_duplicates: list[FinancialRecord] = field(default_factory=list)
    settlement_duplicates: list[FinancialRecord] = field(default_factory=list)
    unmatched_records: list[FinancialRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Refund ids delivered more than once with CONFLICTING amounts.  The
    # refund total is not trustworthy — the case must escalate, never sum
    # the conflicting records as if they were independent refunds.
    refund_conflicts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "match_type": self.match_type,
            "payment_id": self.payment.external_id if self.payment else "",
            "payment": self.payment.to_dict() if self.payment else None,
            "settlement": self.settlement.to_dict() if self.settlement else None,
            "refunds": [r.to_dict() for r in self.refunds],
            "fee_taxes": [r.to_dict() for r in self.fee_taxes],
            "adjustments": [r.to_dict() for r in self.adjustments],
            "context_records": [r.to_dict() for r in self.context_records],
            "payment_duplicates": [r.to_dict() for r in self.payment_duplicates],
            "settlement_duplicates": [r.to_dict() for r in self.settlement_duplicates],
            "unmatched_records": [r.to_dict() for r in self.unmatched_records],
            "notes": list(self.notes),
            "refund_conflicts": list(self.refund_conflicts),
        }


def _records_of_type(records: list[FinancialRecord], record_type: str) -> list[FinancialRecord]:
    return [r for r in records if r.record_type == record_type]


def match_records_for_payment(
    payment_id: str,
    records: list[FinancialRecord],
    order_id: str = "",
) -> MatchResult:
    """Deterministically match all records belonging to one payment.

    Strategy priority (strictly deterministic):
      1. Primary payment record (exact payment_id).
      2. Settlement via exact payment_id reference.
      3. Refunds via exact payment_id reference.
      4. Fee/tax and adjustment records via payment_id or order_id.
      5. Duplicate detection for payment and settlement records.
      6. Unmatched records are reported, never silently dropped.
    """
    result = MatchResult(matched=False, match_type="none")

    payments = _records_of_type(records, RECORD_PAYMENT)
    settlements = _records_of_type(records, RECORD_SETTLEMENT)
    refunds = _records_of_type(records, RECORD_REFUND)
    fee_taxes = _records_of_type(records, RECORD_FEE_TAX)
    adjustments = _records_of_type(records, RECORD_ADJUSTMENT)

    # Tier 5-7 context records (dispute/invoice/payment_link/operational).
    # They are matched to the case by payment_id / order_id like financial
    # records, but the calculator never reads their amounts.
    context_records = [r for r in records if r.record_type in CONTEXT_RECORD_TYPES]

    # ── 1. Primary payment ──
    primary_payments = [p for p in payments if p.external_id == payment_id]
    if primary_payments:
        result.payment = primary_payments[0]
        result.payment_duplicates = primary_payments[1:]
        if result.payment_duplicates:
            result.notes.append(
                f"{len(result.payment_duplicates)} duplicate payment record(s) for {payment_id}"
            )
        result.matched = True
        result.match_type = "payment_id"
    else:
        # Try matching by order_id fallback (payments without exact id match)
        for p in payments:
            if order_id and p.order_id == order_id:
                result.payment = p
                result.match_type = "order_id"
                result.matched = True
                break

    # ── 2. Settlement via exact payment reference ──
    primary_settlements = [
        s for s in settlements
        if s.payment_id == payment_id or (result.payment and s.payment_id == result.payment.external_id)
    ]
    if primary_settlements:
        result.settlement = primary_settlements[0]
        result.settlement_duplicates = primary_settlements[1:]
        if result.settlement_duplicates:
            result.notes.append(
                f"{len(result.settlement_duplicates)} duplicate settlement record(s) for {payment_id}"
            )
        if result.match_type == "none":
            result.match_type = "settlement_reference"
            result.matched = True

    # ── 3. Refunds via exact payment relationship ──
    result.refunds = [
        r for r in refunds
        if r.payment_id == payment_id or (result.payment and r.payment_id == result.payment.external_id)
    ]

    # ── 3b. Contradictory refund evidence (same refund id, different amounts) ──
    # Two records carrying the SAME refund id but DIFFERENT amounts are a
    # genuine contradiction (only one amount can be true).  Each amount is
    # individually valid, so this is NOT invalid source data — it is
    # contradictory evidence that must be surfaced, never silently summed
    # or guessed.  Both records stay visible; the classifier escalates.
    by_refund_id: dict[str, list] = {}
    for r in result.refunds:
        by_refund_id.setdefault(r.external_id, []).append(r)
    for refund_id, group in by_refund_id.items():
        amounts = {r.amount for r in group}
        if len(group) > 1 and len(amounts) > 1:
            result.refund_conflicts.append(refund_id)
            result.notes.append(
                f"Contradictory refund records for refund {refund_id}: recorded "
                f"amounts {sorted(amounts)} differ across {len(group)} deliveries"
            )

    # ── 4. Fee/tax + adjustments via payment_id or order_id ──
    result.fee_taxes = [
        r for r in fee_taxes
        if r.payment_id == payment_id
        or (result.payment and r.payment_id == result.payment.external_id)
        or (order_id and r.order_id == order_id)
    ]
    result.adjustments = [
        r for r in adjustments
        if r.payment_id == payment_id
        or (result.payment and r.payment_id == result.payment.external_id)
        or (order_id and r.order_id == order_id)
    ]

    # ── 4b. Context records: deterministic linkage by payment_id / order_id ──
    result.context_records = [
        r for r in context_records
        if r.payment_id == payment_id
        or (result.payment and r.payment_id == result.payment.external_id)
        or (r.order_id and order_id and r.order_id == order_id)
        or (r.order_id and result.payment and r.order_id == result.payment.order_id)
    ]

    # ── 5. Refund/payment amount-consistency check ──
    if result.refunds:
        refund_total = sum(r.amount for r in result.refunds)
        captured = result.payment.amount if result.payment else 0
        if captured and refund_total > captured:
            result.notes.append(
                f"Refund total {refund_total} exceeds captured amount {captured}"
            )
        if result.match_type == "none":
            result.match_type = "refund_relationship"
            result.matched = True

    # ── 6. Missing-record detection (reported, not auto-resolved) ──
    if result.payment and not result.settlement:
        result.notes.append(f"No settlement record found for payment {payment_id}")
    if not result.payment and settlements:
        result.notes.append(f"No payment record found for payment {payment_id}")

    # ── 7. Unmatched records ──
    matched_ids = set()
    if result.payment:
        matched_ids.add(result.payment.record_id)
    if result.settlement:
        matched_ids.add(result.settlement.record_id)
    for r in result.refunds + result.fee_taxes + result.adjustments + result.context_records:
        matched_ids.add(r.record_id)
    result.unmatched_records = [
        r for r in records
        if r.record_id not in matched_ids and r.record_type not in (RECORD_PAYMENT,)
    ]
    # Context records with no payment/order linkage stay visible as unmatched
    # (evidence preserved — never silently dropped).

    return result


def group_payment_ids(records: list[FinancialRecord]) -> list[str]:
    """Deterministically derive the set of payment ids to reconcile.

    The authoritative payment id set comes from payment records first,
    then from payment_id references on settlement/refund/fee_tax records.
    Sorted for deterministic ordering.
    """
    ids: set[str] = set()
    for r in records:
        if r.record_type == RECORD_PAYMENT and r.external_id:
            ids.add(r.external_id)
        elif r.payment_id:
            ids.add(r.payment_id)
    return sorted(ids)


def detect_duplicate_payloads(records: list[FinancialRecord]) -> list[tuple[str, list[str]]]:
    """Detect records with identical payload hashes (duplicate webhook/events).

    Returns a list of (payload_hash, [record_ids]) for hashes shared by
    more than one record.
    """
    by_hash: dict[str, list[str]] = {}
    for r in records:
        if r.payload_hash:
            by_hash.setdefault(r.payload_hash, []).append(r.record_id)
    return [
        (h, ids) for h, ids in by_hash.items() if len(ids) > 1
    ]