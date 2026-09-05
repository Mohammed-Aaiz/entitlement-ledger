"""Tier 1-7 reconciliation analysis + relationship/evidence graph.

The seven tiers are REAL deterministic domains layered over the same
records the engine already reconciles:

    Tier 1  Payment / Order
    Tier 2  Refund
    Tier 3  Settlement
    Tier 4  Fee / Tax
    Tier 5  Dispute / Risk           (context — never money arithmetic)
    Tier 6  Invoice / Payment Link   (obligation relationships)
    Tier 7  Operational / Integrity  (downtime / account / event integrity)

Money truth stays in the deterministic engine.  Every tier finding is
evidence-backed and derives from actual records — no tier ever invents
financial truth.  Context tiers (5-7) can escalate a deterministically
MATCHED case to REVIEW_REQUIRED through explicit codes (DISPUTE_OPEN /
PARTIAL_PAYMENT / OVERPAYMENT), but can never change amounts.
"""
from __future__ import annotations

from typing import Optional

from .models import (
    FinancialRecord,
    CLASS_MATCHED,
    CLASS_REVIEW_REQUIRED,
    CLASS_EXCEPTION,
    RECORD_PAYMENT,
    RECORD_REFUND,
    RECORD_SETTLEMENT,
    RECORD_FEE_TAX,
    RECORD_ADJUSTMENT,
    RECORD_DISPUTE,
    RECORD_INVOICE,
    RECORD_PAYMENT_LINK,
    RECORD_OPERATIONAL,
    RECORD_TIER,
)

# Exceptions that belong to each financial tier (deterministic outcome map).
TIER_EXCEPTION_CODES: dict[int, set[str]] = {
    1: {"DUPLICATE_PAYMENT", "CONTRADICTORY_EVIDENCE"},
    2: {"REFUND_MISMATCH", "CONTRADICTORY_EVIDENCE"},
    3: {
        "MISSING_SETTLEMENT", "AMOUNT_MISMATCH", "PARTIAL_SETTLEMENT",
        "DUPLICATE_SETTLEMENT", "LATE_SETTLEMENT", "UNRESOLVED_RECONCILIATION",
        "UNLINKED_SETTLEMENT", "AI_UNAVAILABLE",
    },
    4: {"FEE_MISMATCH", "TAX_MISMATCH", "CONTRADICTORY_EVIDENCE"},
    5: {"DISPUTE_OPEN"},
    6: {"PARTIAL_PAYMENT", "OVERPAYMENT"},
    7: set(),
}

TIER_LABELS = {
    1: "Payment / Order",
    2: "Refund",
    3: "Settlement",
    4: "Fee / Tax",
    5: "Dispute / Risk",
    6: "Invoice / Payment Link",
    7: "Operational / Event Integrity",
}

# Dispute statuses that mean the dispute is still open / unresolved.
_OPEN_DISPUTE_STATUSES = {
    "created", "open", "under_review", "action_required", "pending",
    "needs_response", "submitted", "under_review",
}
_CLOSED_DISPUTE_STATUSES = {
    "won", "lost", "closed", "rejected", "accepted", "resolved", "settled",
}


def _evidence_refs(records: list[FinancialRecord]) -> list[str]:
    return sorted({r.raw_evidence_ref for r in records if r.raw_evidence_ref})


def _record_ids(records: list[FinancialRecord]) -> list[str]:
    return [r.record_id for r in records]


def _records_of_type(records: list[FinancialRecord], record_type: str) -> list[FinancialRecord]:
    return [r for r in records if r.record_type == record_type]


def build_relationship_graph(payment_id: str, records: list[FinancialRecord]) -> list[dict]:
    """Reconstruct typed, evidence-backed relationships for one case.

    Every relationship is derived from deterministic identifiers on actual
    records (payment_id / order_id / external ids) — never semantic guesswork.
    """
    relationships: list[dict] = []
    payments = _records_of_type(records, RECORD_PAYMENT)
    payment = payments[0] if payments else None

    def _add(source: str, relation: str, target: str, evidence: list[str]) -> None:
        relationships.append({
            "source": source,
            "relation": relation,
            "target": target,
            "evidence_refs": sorted(set(evidence)),
        })

    pay_node = f"payment:{payment_id}"

    if payment and payment.order_id:
        _add(pay_node, "belongs_to_order", f"order:{payment.order_id}",
             [payment.raw_evidence_ref])
    if payment and len(payments) > 1:
        for dup in payments[1:]:
            _add(pay_node, "duplicate_payment_of", f"payment:{dup.external_id}",
                 [dup.raw_evidence_ref])

    for rec in records:
        if rec.record_type == RECORD_REFUND and rec.payment_id == payment_id:
            _add(pay_node, "has_refund", f"refund:{rec.external_id}", [rec.raw_evidence_ref])
        elif rec.record_type == RECORD_SETTLEMENT and (
            rec.payment_id == payment_id or (payment and rec.payment_id == payment.external_id)
        ):
            _add(pay_node, "settled_by", f"settlement:{rec.external_id}", [rec.raw_evidence_ref])
        elif rec.record_type == RECORD_FEE_TAX:
            _add(pay_node, "charged_fee_tax", f"fee_tax:{rec.external_id}", [rec.raw_evidence_ref])
        elif rec.record_type == RECORD_ADJUSTMENT:
            _add(pay_node, "adjusted_by", f"adjustment:{rec.external_id}", [rec.raw_evidence_ref])
        elif rec.record_type == RECORD_DISPUTE and (
            rec.payment_id == payment_id or (payment and rec.payment_id == payment.external_id)
            or (rec.order_id and payment and rec.order_id == payment.order_id)
        ):
            _add(pay_node, "disputed_by", f"dispute:{rec.external_id}", [rec.raw_evidence_ref])
        elif rec.record_type == RECORD_INVOICE and (
            rec.payment_id == payment_id
            or (rec.order_id and payment and rec.order_id == payment.order_id)
        ):
            _add(pay_node, "invoiced_by", f"invoice:{rec.external_id}", [rec.raw_evidence_ref])
        elif rec.record_type == RECORD_PAYMENT_LINK and (
            rec.payment_id == payment_id
            or (rec.order_id and payment and rec.order_id == payment.order_id)
        ):
            _add(pay_node, "paid_via_link", f"payment_link:{rec.external_id}", [rec.raw_evidence_ref])
        elif rec.record_type == RECORD_OPERATIONAL:
            _add(pay_node, "operational_context", f"event:{rec.external_id}", [rec.raw_evidence_ref])

    return relationships


def _open_disputes(records: list[FinancialRecord]) -> list[FinancialRecord]:
    out = []
    for r in _records_of_type(records, RECORD_DISPUTE):
        status = (r.status or "").lower().strip()
        if status in _OPEN_DISPUTE_STATUSES or (
            status and status not in _CLOSED_DISPUTE_STATUSES
        ):
            out.append(r)
    return out


def _closed_disputes(records: list[FinancialRecord]) -> list[FinancialRecord]:
    return [
        r for r in _records_of_type(records, RECORD_DISPUTE)
        if (r.status or "").lower().strip() in _CLOSED_DISPUTE_STATUSES
    ]


def analyze_case_tiers(
    payment_id: str,
    records: list[FinancialRecord],
    classification: str,
    exception_codes: list[str],
    expected_amount: int = 0,
    actual_amount: int = 0,
    variance: int = 0,
    calculation_trace: Optional[dict] = None,
    match_info: Optional[dict] = None,
) -> dict:
    """Produce structured Tier 1-7 findings for a case.

    Returns {
        "tiers_applied": sorted list of tier ints present,
        "tier_findings": list of finding dicts,
        "escalations": list of escalation codes (DISPUTE_OPEN / PARTIAL_PAYMENT /
                       OVERPAYMENT) that should move a MATCHED case to REVIEW.
    }

    Pure and deterministic: no LLM, no writes.  Context records never change
    financial amounts; they only annotate / escalate review state.
    """
    codes = set(exception_codes or [])
    trace = calculation_trace or {}
    by_type: dict[str, list[FinancialRecord]] = {}
    for rt in (RECORD_PAYMENT, RECORD_REFUND, RECORD_SETTLEMENT, RECORD_FEE_TAX,
               RECORD_ADJUSTMENT, RECORD_DISPUTE, RECORD_INVOICE,
               RECORD_PAYMENT_LINK, RECORD_OPERATIONAL):
        by_type[rt] = _records_of_type(records, rt)

    tiers_present: set[int] = set()
    findings: list[dict] = []
    escalations: list[str] = []

    def _finding(tier: int, code: str, severity: str, explanation: str,
                 refs: Optional[list] = None, detail: Optional[dict] = None) -> None:
        findings.append({
            "tier": tier,
            "tier_label": TIER_LABELS[tier],
            "code": code,
            "severity": severity,
            "explanation": explanation,
            "evidence_refs": sorted(set(refs or [])),
            "detail": detail or {},
        })

    # Determine per-tier deterministic outcome status.
    def _tier_status(tier: int) -> str:
        own = TIER_EXCEPTION_CODES.get(tier, set()) & codes
        if not own:
            # Tier not implicated by any exception.
            return "matched" if classification == CLASS_MATCHED else "ok"
        if classification == CLASS_EXCEPTION:
            return "exception"
        return "review"

    # Tier 3 amounts drive most findings.
    if by_type[RECORD_PAYMENT]:
        tiers_present.add(1)
    if by_type[RECORD_REFUND]:
        tiers_present.add(2)
    if by_type[RECORD_SETTLEMENT] or trace.get("actual_settlement") is not None or codes & {
        "MISSING_SETTLEMENT", "AMOUNT_MISMATCH", "PARTIAL_SETTLEMENT",
        "DUPLICATE_SETTLEMENT", "LATE_SETTLEMENT", "UNLINKED_SETTLEMENT",
        "UNRESOLVED_RECONCILIATION",
    }:
        tiers_present.add(3)
    if by_type[RECORD_FEE_TAX]:
        tiers_present.add(4)
    if by_type[RECORD_DISPUTE]:
        tiers_present.add(5)
    if by_type[RECORD_INVOICE] or by_type[RECORD_PAYMENT_LINK]:
        tiers_present.add(6)
    if by_type[RECORD_OPERATIONAL] or _has_integrity_signals(records):
        tiers_present.add(7)

    # ── Tier 1: Payment / Order ──
    if 1 in tiers_present:
        pays = by_type[RECORD_PAYMENT]
        status = _tier_status(1)
        refs = _evidence_refs(pays)
        if len(pays) > 1 and "DUPLICATE_PAYMENT" in codes:
            _finding(1, "DUPLICATE_PAYMENT",
                     "exception" if classification == CLASS_EXCEPTION else "review",
                     f"{len(pays)} payment records for {payment_id} — a capture is duplicated.",
                     refs, {"duplicate_count": len(pays) - 1})
        else:
            state = pays[0].status if pays else "unknown"
            _finding(1, "PAYMENT_CAPTURE",
                     "review" if state not in ("captured", "authorized") else status,
                     f"Payment {payment_id} state '{state}' with "
                     f"{len(pays)} record(s) and order "
                     f"{(pays[0].order_id or 'none') if pays else 'none'}.",
                     refs, {"status": state, "amount": pays[0].amount if pays else 0})

    # ── Tier 2: Refund ──
    if 2 in tiers_present:
        refunds = by_type[RECORD_REFUND]
        total = sum(r.amount for r in refunds)
        captured = by_type[RECORD_PAYMENT][0].amount if by_type[RECORD_PAYMENT] else 0
        overage = captured and total > captured
        status = "exception" if "REFUND_MISMATCH" in codes else (
            "review" if "CONTRADICTORY_EVIDENCE" in codes else (
                "matched" if classification == CLASS_MATCHED else "ok"))
        _finding(2, "REFUND_SUMMARY",
                 "exception" if overage else status,
                 f"{len(refunds)} refund record(s) totalling {total} paise "
                 f"against capture {captured} paise."
                 + (" Refunds exceed the captured amount." if overage else ""),
                 _evidence_refs(refunds),
                 {"refund_total": total, "captured_amount": captured,
                  "refund_count": len(refunds)})

    # ── Tier 3: Settlement ──
    if 3 in tiers_present:
        refs = _evidence_refs(by_type[RECORD_SETTLEMENT]) or _evidence_refs(records)
        if "UNLINKED_SETTLEMENT" in codes:
            _finding(3, "UNLINKED_SETTLEMENT", "exception",
                     "Settlement could not be linked deterministically to a payment; "
                     "the settlement is preserved as evidence.",
                     refs, {"settlement_id": payment_id})
        elif variance is not None and variance != 0:
            severity = "exception" if classification == CLASS_EXCEPTION else "review"
            label = "OVER_SETTLEMENT" if variance > 0 else (
                "PARTIAL_SETTLEMENT" if "PARTIAL_SETTLEMENT" in codes else "AMOUNT_MISMATCH")
            direction = ("above expected (over-settlement)"
                         if variance > 0 else "below expected")
            _finding(3, label, severity,
                     f"Settlement variance is {variance:+d} paise "
                     f"({direction}): expected {expected_amount}, actual {actual_amount}.",
                     refs, {"expected": expected_amount, "actual": actual_amount,
                            "variance": variance})
        elif "MISSING_SETTLEMENT" in codes:
            _finding(3, "MISSING_SETTLEMENT", "review",
                     f"No settlement received for {payment_id} (expected "
                     f"{expected_amount} paise).",
                     refs, {"expected": expected_amount})
        elif "LATE_SETTLEMENT" in codes:
            _finding(3, "LATE_SETTLEMENT", "review",
                     "Settlement arrived after the expected window.",
                     refs, {})
        elif "DUPLICATE_SETTLEMENT" in codes:
            _finding(3, "DUPLICATE_SETTLEMENT", "exception",
                     "More than one settlement record exists for this payment.",
                     refs, {"settlement_count": len(by_type[RECORD_SETTLEMENT])})
        else:
            _finding(3, "SETTLEMENT_MATCHED", "matched",
                     f"Settlement reconciles exactly: expected {expected_amount} paise "
                     f"matches actual {actual_amount} paise (variance 0).",
                     refs, {"expected": expected_amount, "actual": actual_amount,
                            "variance": 0})

    # ── Tier 4: Fee / Tax ──
    if 4 in tiers_present:
        fts = by_type[RECORD_FEE_TAX]
        fee_total = sum(r.fee_amount for r in fts)
        tax_total = sum(r.tax_amount for r in fts)
        captured = by_type[RECORD_PAYMENT][0].amount if by_type[RECORD_PAYMENT] else 0
        if "FEE_MISMATCH" in codes or "TAX_MISMATCH" in codes:
            _finding(4, "FEE_TAX_MISMATCH",
                     "exception" if classification == CLASS_EXCEPTION else "review",
                     f"Recorded fee/tax evidence is internally inconsistent "
                     f"(fee total {fee_total}, tax total {tax_total} paise).",
                     _evidence_refs(fts),
                     {"fee_total": fee_total, "tax_total": tax_total})
        else:
            _finding(4, "FEE_TAX_EVIDENCE", "matched" if classification == CLASS_MATCHED else "ok",
                     f"{len(fts)} fee/tax record(s): fees {fee_total} paise, "
                     f"taxes {tax_total} paise against capture {captured} paise.",
                     _evidence_refs(fts),
                     {"fee_total": fee_total, "tax_total": tax_total,
                      "captured_amount": captured})

    # ── Tier 5: Dispute / Risk (context only) ──
    if 5 in tiers_present:
        open_ds = _open_disputes(records)
        closed_ds = _closed_disputes(records)
        if open_ds:
            esc_refs = _evidence_refs(open_ds)
            _finding(5, "DISPUTE_OPEN", "action",
                     f"{len(open_ds)} open dispute(s) attached to {payment_id} "
                     "— risk context only; settlement arithmetic unchanged, "
                     "but the case should be reviewed.",
                     esc_refs, {"open_count": len(open_ds),
                                "statuses": sorted({d.status for d in open_ds})})
            escalations.append("DISPUTE_OPEN")
        elif closed_ds:
            _finding(5, "DISPUTE_CLOSED", "info",
                     f"{len(closed_ds)} dispute(s) resolved "
                     f"({', '.join(sorted({d.status for d in closed_ds}))}).",
                     _evidence_refs(closed_ds), {"closed_count": len(closed_ds)})

    # ── Tier 6: Invoice / Payment Link obligation ──
    if 6 in tiers_present:
        obligations = by_type[RECORD_INVOICE] + by_type[RECORD_PAYMENT_LINK]
        # Paid total must be computed PER-OBLIGATION from the payments linked to
        # that obligation (by payment_id or order_id), NOT the sum of every
        # payment record in the case.  Summing all payment records would
        # double-count unrelated/duplicate captures and falsely escalate
        # OVERPAYMENT when the linked payment total actually equals the
        # obligation (e.g. pay_R094 / pay_R095 style cases).
        for ob in obligations:
            owed = ob.amount or 0
            status_s = (ob.status or "").lower().strip()
            refs = [ob.raw_evidence_ref]
            linked_payments = [
                r for r in by_type[RECORD_PAYMENT]
                if r.payment_id == payment_id or r.order_id == ob.order_id
            ]
            paid_total = sum(r.amount for r in linked_payments)
            if owed and paid_total:
                if paid_total < owed:
                    _finding(6, "PARTIAL_PAYMENT", "action",
                             f"Obligation {ob.external_id} worth {owed} paise is only "
                             f"{paid_total} paise paid — partial payment.",
                             refs, {"owed": owed, "paid": paid_total})
                    escalations.append("PARTIAL_PAYMENT")
                elif paid_total > owed:
                    _finding(6, "OVERPAYMENT", "action",
                             f"Payments totalling {paid_total} paise exceed obligation "
                             f"{owed} paise for {ob.external_id}.",
                             refs, {"owed": owed, "paid": paid_total})
                    escalations.append("OVERPAYMENT")
                else:
                    _finding(6, "OBLIGATION_PAID", "matched",
                             f"Obligation {ob.external_id} fully paid: {paid_total} paise.",
                             refs, {"owed": owed, "paid": paid_total})
            else:
                lifecycle = "expired" if "expired" in status_s else (
                    "cancelled" if "cancelled" in status_s else status_s or "unknown")
                sev = "info"
                if lifecycle in ("expired", "cancelled") and paid_total == 0:
                    sev = "info"
                _finding(6, "OBLIGATION_STATE", sev,
                         f"Obligation {ob.external_id} lifecycle '{lifecycle}' "
                         f"(amount {owed} paise); paid {paid_total} paise in this case.",
                         refs, {"lifecycle": lifecycle, "owed": owed, "paid": paid_total})

    # ── Tier 7: Operational / Event Integrity (context only) ──
    if 7 in tiers_present:
        ops = by_type[RECORD_OPERATIONAL]
        if ops:
            families = sorted({(r.extra or {}).get("family", "operational") for r in ops})
            _finding(7, "OPERATIONAL_CONTEXT", "info",
                     f"{len(ops)} operational event(s) present "
                     f"({', '.join(families)}) — context only.",
                     _evidence_refs(ops), {"families": families})
        integrity = _integrity_signals(records)
        if integrity:
            _finding(7, "EVENT_INTEGRITY", "review",
                     "Event-sequence anomalies detected: " + "; ".join(integrity) + ".",
                     _evidence_refs(records), {"signals": integrity})

    return {
        "tiers_applied": sorted(tiers_present),
        "tier_findings": findings,
        "escalations": sorted(set(escalations)),
    }


def _has_integrity_signals(records: list[FinancialRecord]) -> bool:
    return bool(_integrity_signals(records))


def _integrity_signals(records: list[FinancialRecord]) -> list[str]:
    """Deterministic event-sequence / integrity signals (Tier 7).

    Detects ordering anomalies that a human would want context for, e.g. a
    refund recorded before the payment capture, or a settlement that predates
    its capture.  Informational only — never financial truth.
    """
    signals: list[str] = []
    payments = _records_of_type(records, RECORD_PAYMENT)
    refunds = _records_of_type(records, RECORD_REFUND)
    settlements = _records_of_type(records, RECORD_SETTLEMENT)
    capture_ts = None
    if payments:
        try:
            from datetime import datetime
            capture_ts = datetime.fromisoformat(
                (payments[0].recorded_at or "").replace("Z", "+00:00")
            ) if payments[0].recorded_at else None
        except ValueError:
            capture_ts = None

    def _ts(rec) -> Optional[object]:
        try:
            from datetime import datetime
            return datetime.fromisoformat((rec.recorded_at or "").replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    if capture_ts:
        for r in refunds + settlements:
            t = _ts(r)
            if t is not None and t < capture_ts:
                signals.append(
                    f"{r.record_type} {r.external_id} recorded before the capture"
                )
    return signals


def apply_escalations(
    classification: str,
    exception_codes: list[str],
    escalations: list[str],
) -> tuple[Optional[str], Optional[str]]:
    """Return (escalation_code, new_classification) when a MATCHED case must
    be moved to REVIEW due to a context-tier action.

    Context findings never change amounts and never create EXCEPTION — the
    deterministic financial outcome stands; only the review state escalates.
    """
    if classification != CLASS_MATCHED or not escalations:
        return None, None
    # Priority: dispute risk first, then obligation mismatches.
    if "DISPUTE_OPEN" in escalations:
        return "DISPUTE_OPEN", CLASS_REVIEW_REQUIRED
    if "PARTIAL_PAYMENT" in escalations:
        return "PARTIAL_PAYMENT", CLASS_REVIEW_REQUIRED
    if "OVERPAYMENT" in escalations:
        return "OVERPAYMENT", CLASS_REVIEW_REQUIRED
    return None, None
