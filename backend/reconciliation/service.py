"""Batch reconciliation service.

Orchestrates the full finance loop:
    normalize → ingest (idempotent) → match → calculate →
    AI interpretation (optional, failure-safe) → decision gate →
    persist case → write decision to the hash-chain ledger

The service is testable WITHOUT an LLM: AI interpretation is optional and
its failure never changes the deterministic outcome.
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
import uuid
from typing import Optional

from .calculator import (
    calculate_expected_settlement,
    compare_settlement,
    FinancialValidationError,
    validate_currency,
)
from .classifier import decide
from .exceptions import (
    ExceptionCode,
    invalid_record,
    invalid_refund_total,
    invalid_amount,
    calculation_error,
    contradictory_financial_evidence,
    unresolved_reconciliation,
)
from .matcher import match_records_for_payment, group_payment_ids, detect_duplicate_payloads
from .models import (
    FinancialRecord,
    ReconciliationCase,
    ReconciliationRun,
    CLASS_MATCHED,
    CLASS_REVIEW_REQUIRED,
    CLASS_EXCEPTION,
    AI_NOT_NEEDED,
    AI_AVAILABLE,
    AI_UNAVAILABLE,
    AI_FAILED,
    ALL_RECORD_TYPES,
    CONTEXT_RECORD_TYPES,
    RECORD_PAYMENT,
    RECORD_REFUND,
    RECORD_SETTLEMENT,
    RECORD_FEE_TAX,
    RECORD_ADJUSTMENT,
)
from hash_chain import compute_decision_hash

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ingestion: normalization + validation + idempotent dedup
# ---------------------------------------------------------------------------

def normalize_record(data: dict) -> FinancialRecord:
    """Normalize and validate one raw record dict into a FinancialRecord.

    Raises FinancialValidationError on malformed records.
    """
    record_type = (data.get("record_type") or "").strip().lower()
    if record_type not in ALL_RECORD_TYPES:
        raise FinancialValidationError(
            f"record_type must be one of {sorted(ALL_RECORD_TYPES)}, got '{record_type}'"
        )

    external_id = str(data.get("external_id") or "").strip()
    if not external_id:
        raise FinancialValidationError("external_id is required")

    try:
        amount = int(data.get("amount", 0) or 0)
    except (TypeError, ValueError):
        raise FinancialValidationError(f"amount must be an integer, got {data.get('amount')!r}")

    currency = validate_currency(data.get("currency", "INR"))

    # Tier 5-7 context records (dispute/invoice/payment_link/operational)
    # are evidence, not financial-capture records — an amount is optional.
    # A financial record without an amount is still rejected.
    if data.get("amount") is None and record_type not in CONTEXT_RECORD_TYPES:
        raise FinancialValidationError(f"record {external_id}: amount is required")

    rec = FinancialRecord.from_dict({
        **data,
        "record_type": record_type,
        "external_id": external_id,
        "amount": amount,
        "currency": currency,
    })

    if amount < 0:
        raise FinancialValidationError(
            f"record {external_id}: amount cannot be negative ({amount} paise)"
        )

    if record_type == RECORD_FEE_TAX:
        fee = int(data.get("fee_amount", 0) or 0)
        tax = int(data.get("tax_amount", 0) or 0)
        if fee < 0 or tax < 0:
            raise FinancialValidationError(
                f"record {external_id}: fee_amount/tax_amount cannot be negative"
            )
        rec.fee_amount = fee
        rec.tax_amount = tax

    if record_type == RECORD_ADJUSTMENT:
        sign = (data.get("adjustment_sign") or "").strip().lower()
        if sign not in ("positive", "negative"):
            raise FinancialValidationError(
                f"adjustment record {external_id} requires adjustment_sign positive|negative"
            )
        rec.adjustment_sign = sign

    if record_type in (RECORD_REFUND, RECORD_SETTLEMENT) and not rec.payment_id:
        raise FinancialValidationError(
            f"{record_type} record {external_id} requires payment_id"
        )

    return rec


def ingest_records(
    tenant_id: str,
    records_data: list[dict],
    source: str = "batch",
) -> tuple[list[FinancialRecord], list[dict], int]:
    """Sync entry point — see ingest_records_async."""
    loop = _new_loop()
    try:
        return loop.run_until_complete(
            ingest_records_async(tenant_id, records_data, source=source)
        )
    finally:
        loop.close()


async def ingest_records_async(
    tenant_id: str,
    records_data: list[dict],
    source: str = "batch",
    db=None,
) -> tuple[list[FinancialRecord], list[dict], int]:
    """Normalize, validate, dedup, and persist records idempotently.

    Returns (valid_records, errors, duplicate_count).

    Idempotency: records sharing (source, external_id, payload_hash) are
    ingested once; identical duplicates are counted, not re-inserted.
    Duplicate webhooks therefore never create two financial decisions.
    """
    valid: list[FinancialRecord] = []
    errors: list[dict] = []
    seen_keys: dict[tuple, str] = {}
    duplicate_count = 0

    for i, data in enumerate(records_data):
        try:
            rec = normalize_record({**data, "source": data.get("source") or source})
        except FinancialValidationError as e:
            errors.append({
                "index": i,
                "external_id": data.get("external_id", "?"),
                "code": ExceptionCode.INVALID_RECORD.value,
                "detail": str(e),
            })
            continue

        key = (rec.source, rec.external_id, rec.payload_hash or "")
        if key in seen_keys:
            duplicate_count += 1
            continue
        seen_keys[key] = rec.record_id
        valid.append(rec)

    if valid:
        await _persist_records(tenant_id, valid, db)

    return valid, errors, duplicate_count


async def _persist_records(tenant_id: str, records: list[FinancialRecord], db=None) -> None:
    """Upsert records into reconciliation_records (idempotent by unique key)."""
    from database import get_db
    own = db is None
    if own:
        db = await get_db()
    try:
        for rec in records:
            await db.execute(
                "INSERT INTO reconciliation_records "
                "(record_id, tenant_id, record_type, external_id, amount, currency, status, "
                " payment_id, order_id, fee_amount, tax_amount, adjustment_sign, recorded_at, "
                " source, raw_evidence_ref, payload_hash, extra, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(record_id) DO UPDATE SET "
                "amount=excluded.amount, status=excluded.status, recorded_at=excluded.recorded_at, "
                "raw_evidence_ref=excluded.raw_evidence_ref, payload_hash=excluded.payload_hash, "
                "extra=excluded.extra",
                (
                    rec.record_id, tenant_id, rec.record_type, rec.external_id, rec.amount,
                    rec.currency, rec.status, rec.payment_id, rec.order_id,
                    rec.fee_amount, rec.tax_amount, rec.adjustment_sign, rec.recorded_at,
                    rec.source, rec.raw_evidence_ref, rec.payload_hash,
                    json.dumps(rec.extra), rec.created_at,
                ),
            )
        await db.commit()
    finally:
        if own:
            await db.close()


# ---------------------------------------------------------------------------
# Per-payment reconciliation (pure, no DB)
# ---------------------------------------------------------------------------

def _dedup_idempotent_payments(records: list[FinancialRecord]) -> list[FinancialRecord]:
    """Drop identical webhook re-deliveries (same external_id + payload hash).

    Only records with a NON-EMPTY payload_hash are deduped — an empty hash
    means we cannot prove the records are the same event, so both are kept
    and the gate handles genuine duplicates.
    """
    seen: set[tuple] = set()
    out: list[FinancialRecord] = []
    for r in records:
        if r.record_type == RECORD_PAYMENT and r.payload_hash:
            key = (r.external_id, r.payload_hash)
            if key in seen:
                continue
            seen.add(key)
        out.append(r)
    return out


def _compute_adjustments(records: list[FinancialRecord]) -> int:
    """Signed net adjustments in paise (positive credits merchant)."""
    total = 0
    for r in records:
        if r.record_type == RECORD_ADJUSTMENT:
            if r.adjustment_sign == "positive":
                total += r.amount
            else:
                total -= r.amount
    return total


def _detect_capture_conflict(records: list[FinancialRecord]) -> bool:
    """Detect the deterministic ambiguity signal for AI-unavailable cases."""
    return any(bool(r.extra.get("capture_conflict")) for r in records)


def _attach_tiers_and_relationships(case: ReconciliationCase, records: list[FinancialRecord]) -> ReconciliationCase:
    """Attach the Tier 1-7 analysis + relationship graph to a case (in place).

    Called on EVERY case return path so tier findings and the typed
    relationship graph are always available for the Control Room.
    """
    from .tiers import (
        build_relationship_graph,
        analyze_case_tiers,
        apply_escalations,
    )
    from .exceptions import open_dispute, partial_payment, overpayment

    if not case.relationships:
        case.relationships = build_relationship_graph(case.payment_id, records)

    analysis = analyze_case_tiers(
        payment_id=case.payment_id,
        records=records,
        classification=case.classification,
        exception_codes=case.exception_codes,
        expected_amount=case.expected_amount,
        actual_amount=case.actual_amount,
        variance=case.variance,
        calculation_trace=case.calculation_trace,
        match_info=case.match_info,
    )
    case.tiers_applied = analysis["tiers_applied"]
    case.tier_findings = analysis["tier_findings"]

    # Context-tier escalation: a deterministically MATCHED case with an open
    # dispute / partial obligation moves to REVIEW — never to EXCEPTION and
    # never changing amounts.
    code, new_class = apply_escalations(
        case.classification, case.exception_codes, analysis["escalations"],
    )
    if code and new_class:
        evidence_refs = sorted({
            r.raw_evidence_ref for r in records if r.raw_evidence_ref
        })
        record_ids = [r.record_id for r in records]
        if code == "DISPUTE_OPEN":
            exc = open_dispute(case.payment_id, record_ids, evidence_refs)
        elif code == "PARTIAL_PAYMENT":
            exc = partial_payment(case.payment_id, case.expected_amount,
                                  case.actual_amount, record_ids, evidence_refs)
        else:
            exc = overpayment(case.payment_id, case.expected_amount,
                              case.actual_amount, record_ids, evidence_refs)
        case.classification = new_class
        case.exception_codes = list(case.exception_codes) + [exc.code]
        case.exceptions = list(case.exceptions) + [exc.to_dict()]
        case.explanation = exc.explanation
    return case


def _classify_financial_error(
    payment_id: str,
    financial_error: str,
    captured: int,
    refund_total: int,
    fee_total: int,
    tax_total: int,
    record_ids: list[str],
    evidence_refs: list[str],
) -> tuple[str, "object"]:
    """Map a deterministic calculation failure to a first-class exception.

    Invalid financial source data becomes a structured, visible outcome with
    the exact invariant violation — never merely a developer log.  The
    returned code is the single authoritative exception for the case.

    Deterministic mapping (checked in order):
      1. refunds > captured        → INVALID_REFUND_TOTAL
      2. fees > captured           → INVALID_FEE_AMOUNT
      3. taxes > captured          → INVALID_TAX_AMOUNT
      4. captured invalid (<=0)    → INVALID_CAPTURED_AMOUNT
      5. otherwise                 → CALCULATION_ERROR
    """
    from .exceptions import (
        invalid_refund_total as _irt,
        invalid_amount as _ia,
        calculation_error as _ce,
        ExceptionCode as _EC,
    )
    if captured > 0 and refund_total > captured:
        exc = _irt(payment_id, captured, refund_total, record_ids, evidence_refs)
        return _EC.INVALID_REFUND_TOTAL.value, exc
    if captured > 0 and fee_total > captured:
        exc = _ia(_EC.INVALID_FEE_AMOUNT.value, payment_id, "fee", fee_total,
                  record_ids, evidence_refs)
        return _EC.INVALID_FEE_AMOUNT.value, exc
    if captured > 0 and tax_total > captured:
        exc = _ia(_EC.INVALID_TAX_AMOUNT.value, payment_id, "tax", tax_total,
                  record_ids, evidence_refs)
        return _EC.INVALID_TAX_AMOUNT.value, exc
    if captured <= 0:
        exc = _ia(_EC.INVALID_CAPTURED_AMOUNT.value, payment_id, "captured amount",
                  captured, record_ids, evidence_refs)
        return _EC.INVALID_CAPTURED_AMOUNT.value, exc
    # Any other calculation failure (e.g. negative expected settlement that
    # is not a simple over-refund/fee/tax case) — preserve the exact detail.
    exc = _ce(payment_id, financial_error, record_ids, evidence_refs)
    return _EC.CALCULATION_ERROR.value, exc


def reconcile_payment(
    tenant_id: str,
    payment_id: str,
    records: list[FinancialRecord],
    use_ai: bool = False,
    provider=None,
    order_id: str = "",
    force_ai: bool = False,
) -> ReconciliationCase:
    """Run the full reconciliation loop for one payment (pure + DB-free).

    Returns a ReconciliationCase with classification, exceptions, trace,
    and AI interpretation metadata.  Never raises for financial anomalies
    — it converts them into structured exceptions.

    AI invocation policy (demand-driven): when use_ai=True the
    deterministic AI gate decides per case whether AI interpretation is
    genuinely needed.  Clear deterministic cases (exact matches, missing
    records, duplicates, invalid records) stay AI-free.  force_ai=True
    bypasses the gate and invokes the provider for every case that reaches
    the AI block — used ONLY by failure-safety benchmarks/tests that must
    exercise the provider on all applicable cases.
    """
    case = ReconciliationCase(payment_id=payment_id)
    case.records = records

    # ── 0. Idempotent dedup of identical webhook deliveries ──
    # A payment record re-delivered with the SAME non-empty payload hash is
    # the same event arriving twice — keep one, never two decisions.
    # Records with different/empty hashes are kept: differing captures are
    # genuine DUPLICATE_PAYMENT material, handled by the gate.
    records = _dedup_idempotent_payments(records)

    # ── 1. Deterministic matching ──
    match = match_records_for_payment(payment_id, records, order_id=order_id)
    case.match_info = match.to_dict()

    # ── 1b. Missing payment is decided BEFORE any calculation ──
    # Without a capture there is no captured amount; the case is a
    # MISSING_PAYMENT exception, never an auto-resolved match.
    if match.payment is None:
        from .exceptions import missing_payment as missing_payment_exc
        from .exceptions import unlinked_settlement as unlinked_exc
        evidence_refs = sorted({r.raw_evidence_ref for r in records if r.raw_evidence_ref})
        exc = missing_payment_exc(
            payment_id,
            [r.record_id for r in records],
            evidence_refs,
        )
        case.classification = CLASS_EXCEPTION
        case.exception_codes = [exc.code]
        case.exceptions = [exc.to_dict()]
        case.explanation = exc.explanation
        # A settlement with no payment is an UNLINKED_SETTLEMENT — preserved
        # as evidence, never attached to a guessed payment relationship.
        if any(r.extra.get("unlinked_settlement") for r in records):
            uexc = unlinked_exc(payment_id, [r.record_id for r in records], evidence_refs)
            case.exception_codes.append(uexc.code)
            case.exceptions.append(uexc.to_dict())
            case.explanation = uexc.explanation
        return _attach_tiers_and_relationships(case, records)

    # ── 2. Deterministic calculation ──
    captured = match.payment.amount
    refund_total = sum(r.amount for r in match.refunds)
    fee_total = sum(ft.fee_amount for ft in match.fee_taxes)
    tax_total = sum(ft.tax_amount for ft in match.fee_taxes)
    adjustments = _compute_adjustments(match.adjustments)

    financial_error: Optional[str] = None
    calculation = None
    try:
        calculation = calculate_expected_settlement(
            captured_amount=captured,
            refund_total=refund_total,
            fee_total=fee_total,
            tax_total=tax_total,
            adjustments=adjustments,
            step_labels={
                "refunds": "Total refunds (from refund records)",
                "fees": "Total fees (from fee/tax records)",
                "taxes": "Total taxes (from fee/tax records)",
                "adjustments": "Net adjustments",
            },
        )
        if match.settlement is not None:
            calculation = compare_settlement(calculation, match.settlement.amount)
    except FinancialValidationError as e:
        financial_error = str(e)
        logger.warning("Reconciliation calculation failed for %s: %s", payment_id, financial_error)

    # ── 3. AI investigation (optional, advisory, failure-safe) ──
    ai_status = AI_NOT_NEEDED
    ai_invoked = False  # tracks whether the provider was GENUINELY invoked
    ai_confidence: Optional[float] = None
    ai_interpretation: dict = {}
    ai_technical_reason = ""
    ai_trigger_reason = ""
    ai_tool_calls = 0

    capture_conflict = _detect_capture_conflict(records)
    if not use_ai:
        ai_trigger_reason = "AI investigation not requested (deterministic-only run)"
    # ── Deterministic AI gate ──
    # AI interpretation runs ONLY when explicitly requested (use_ai) AND the
    # gate decides the case genuinely needs investigation.  Clear
    # deterministic cases remain AI-free (demand-driven usage).  In
    # deterministic-only mode an ambiguous capture_conflict case is decided
    # conservatively (REVIEW_REQUIRED / AI_UNAVAILABLE) WITHOUT any provider
    # call — the pipeline never depends on a network round-trip to stay safe.
    if use_ai:
        from .ai_controller import investigate_case, should_investigate

        # Compute the pre-AI deterministic classification so the gate can
        # decide from REAL exception codes (no duplicated business logic).
        pre_gate = None
        if calculation is not None:
            pre_gate = decide(
                payment_id=payment_id,
                match=match,
                calculation=calculation,
                settlement_due_within_days=7,
                payment_recorded_at=match.payment.recorded_at if match.payment else None,
                ai_status=AI_NOT_NEEDED,
                ai_confidence=None,
                ai_technical_reason="",
                force_review=False,
            )

        if force_ai:
            invoke_ai = True
            ai_trigger_reason = "AI investigation requested (force_ai — failure-safety mode)"
        else:
            invoke_ai, ai_trigger_reason = should_investigate(
                exception_codes=pre_gate.exception_codes if pre_gate else [],
                capture_conflict=capture_conflict,
                has_payment=match.payment is not None,
                financial_error=financial_error is not None,
                variance=calculation.variance if calculation else 0,
            )

        if invoke_ai:
            # The context is the ONLY data the investigator can read: the
            # normalized, tenant-scoped records of this case.  Tools never
            # touch the database — no cross-tenant surface exists.
            context = {
                "payment_id": payment_id,
                "records": [
                    {
                        "record_type": r.record_type,
                        "external_id": r.external_id,
                        "record_id": r.record_id,
                        "amount": r.amount,
                        "status": r.status,
                        "payment_id": r.payment_id,
                        "order_id": r.order_id,
                        "fee_amount": r.fee_amount,
                        "tax_amount": r.tax_amount,
                        "adjustment_sign": r.adjustment_sign,
                        "recorded_at": r.recorded_at,
                        "source": r.source,
                        "raw_evidence_ref": r.raw_evidence_ref,
                    }
                    for r in records
                ],
                "deterministic_expected_settlement": (
                    calculation.expected_settlement if calculation else None
                ),
                "deterministic_actual_settlement": (
                    calculation.actual_settlement if calculation else None
                ),
                "deterministic_variance": (
                    calculation.variance if calculation else None
                ),
                "match_notes": match.notes,
                "capture_conflict": capture_conflict,
            }
            result = investigate_case(context, provider=provider)
            ai_invoked = True
            ai_status = result.status
            ai_confidence = (result.interpretation or {}).get("confidence") if result.interpretation else None
            ai_interpretation = result.interpretation or {}
            ai_technical_reason = result.technical_reason
            ai_tool_calls = result.tool_call_count
        else:
            # Gate decided the case resolves deterministically — no provider
            # call, honest not-needed status with the trigger reason.
            ai_status = AI_NOT_NEEDED
            ai_trigger_reason = ai_trigger_reason or "deterministic resolution — no AI required"

    # ── 4. Deterministic decision gate ──
    if financial_error is not None:
        # Invalid source data is a FIRST-CLASS structured outcome — never a
        # log-only warning.  The exact invariant violation is mapped to a
        # specific deterministic exception code with full reasoning.  AI can
        # never "repair" these amounts; the source evidence must be fixed.
        evidence_refs = sorted({r.raw_evidence_ref for r in records if r.raw_evidence_ref})
        record_ids = [r.record_id for r in records]
        code, exc = _classify_financial_error(
            payment_id=payment_id,
            financial_error=financial_error,
            captured=captured,
            refund_total=refund_total,
            fee_total=fee_total,
            tax_total=tax_total,
            record_ids=record_ids,
            evidence_refs=evidence_refs,
        )
        case.classification = CLASS_EXCEPTION
        case.exception_codes = [code]
        case.exceptions = [exc.to_dict()]
        case.explanation = exc.explanation
        case.ai_status = ai_status
        case.ai_invoked = ai_invoked
        case.ai_confidence = ai_confidence
        case.ai_interpretation = ai_interpretation
        case.ai_technical_reason = ai_technical_reason
        case.ai_trigger_reason = ai_trigger_reason
        case.ai_tool_calls = ai_tool_calls
        if calculation is not None:
            case.calculation_trace = calculation.to_dict()
        return _attach_tiers_and_relationships(case, records)

    if calculation is None:
        # No payment record → no captured amount → cannot compute.
        evidence_refs = sorted({r.raw_evidence_ref for r in records if r.raw_evidence_ref})
        exc = unresolved_reconciliation(
            payment_id, "No valid payment record to compute expected settlement",
            [r.record_id for r in records], evidence_refs,
        )
        case.classification = CLASS_EXCEPTION
        case.exception_codes = [exc.code]
        case.exceptions = [exc.to_dict()]
        case.explanation = exc.explanation
        case.ai_status = ai_status
        case.ai_invoked = ai_invoked
        case.ai_confidence = ai_confidence
        case.ai_interpretation = ai_interpretation
        case.ai_technical_reason = ai_technical_reason
        case.ai_trigger_reason = ai_trigger_reason
        case.ai_tool_calls = ai_tool_calls
        return _attach_tiers_and_relationships(case, records)

    # Gate input: deterministic evidence must be sufficient to approve.
    # Ambiguous capture-conflict cases (or AI requested but unavailable /
    # failed) force REVIEW_REQUIRED — never a guess → approval.
    force_review = capture_conflict
    if use_ai and ai_status in (AI_UNAVAILABLE, AI_FAILED):
        force_review = True
        # Record the AI_UNAVAILABLE exception through the gate when the
        # deterministic evidence was not independently sufficient.
    if not use_ai and capture_conflict:
        # Deterministic-only mode: mark AI as unavailable for this ambiguous
        # case so the gate emits AI_UNAVAILABLE without a provider call.
        ai_status = AI_UNAVAILABLE
        ai_technical_reason = "AI interpretation not requested (deterministic-only mode)"
        ai_trigger_reason = "capture conflict detected — AI interpretation not requested"

    gate = decide(
        payment_id=payment_id,
        match=match,
        calculation=calculation,
        settlement_due_within_days=7,
        payment_recorded_at=match.payment.recorded_at if match.payment else None,
        ai_status=ai_status,
        ai_confidence=ai_confidence,
        ai_technical_reason=ai_technical_reason,
        force_review=force_review,
    )

    case.classification = gate.classification
    case.exception_codes = gate.exception_codes
    case.exceptions = gate.exceptions
    case.explanation = gate.explanation
    case.expected_amount = calculation.expected_settlement
    case.actual_amount = calculation.actual_settlement or 0
    case.variance = calculation.variance or 0
    case.calculation_trace = calculation.to_dict()
    case.ai_status = ai_status
    case.ai_invoked = ai_invoked
    case.ai_confidence = ai_confidence
    case.ai_interpretation = ai_interpretation
    case.ai_technical_reason = ai_technical_reason
    case.ai_trigger_reason = ai_trigger_reason
    case.ai_tool_calls = ai_tool_calls

    return _attach_tiers_and_relationships(case, records)


# ---------------------------------------------------------------------------
# Ledger integration
# ---------------------------------------------------------------------------

def _build_ledger_decision(case: ReconciliationCase, prev_hash: str) -> Optional[dict]:
    """Build a hash-chained ledger decision from a reconciliation case.

    Returns None when no financial decision can be recorded (no captured
    amount — e.g. MISSING_PAYMENT / INVALID_RECORD).
    """
    trace = case.calculation_trace
    captured = trace.get("captured_amount", 0) if trace else 0
    if captured <= 0:
        return None

    from models import LineItem

    line_items = []
    if trace:
        refunds = trace.get("refund_total", 0)
        fees = trace.get("fee_total", 0)
        taxes = trace.get("tax_total", 0)
        adjustments = trace.get("adjustments", 0)
        if fees:
            line_items.append(LineItem(label="Razorpay fee", amount=fees, type="fee",
                                       policy_clause_id="razorpay_fee", evidence_ids=[]).model_dump())
        if taxes:
            line_items.append(LineItem(label="GST on fee", amount=taxes, type="deduction",
                                       policy_clause_id="razorpay_tax", evidence_ids=[]).model_dump())
        if refunds:
            line_items.append(LineItem(label="Refunds", amount=refunds, type="deduction",
                                       policy_clause_id="razorpay_refunds", evidence_ids=[]).model_dump())
        if adjustments:
            line_items.append(LineItem(
                label="Adjustments", amount=abs(adjustments),
                type="credit" if adjustments > 0 else "deduction",
                policy_clause_id="razorpay_adjustments", evidence_ids=[],
            ).model_dump())

    # Cross-check: the existing deterministic engine must reproduce the
    # reconciliation expected settlement exactly.
    from calculations import calculate_final_amount
    recomputed = calculate_final_amount(captured, [LineItem(**li) for li in line_items])
    expected = trace.get("expected_settlement", 0)
    if recomputed != expected:
        logger.error(
            "Ledger cross-check failed for case %s: engine=%d, reconciliation=%d",
            case.case_id, recomputed, expected,
        )
        raise FinancialValidationError(
            f"Ledger cross-check mismatch: engine {recomputed} != reconciliation {expected}"
        )

    status = "APPROVED" if case.classification == CLASS_MATCHED else "REVIEW_REQUIRED"
    now = case.created_at
    decision_data = {
        "decision_id": f"decr_{uuid.uuid4().hex[:10]}",
        "entity_type": "reconciliation",
        "entity_id": case.payment_id,
        "gross_amount": captured,
        "line_items": line_items,
        "final_amount": expected,
        "policy_version_id": "reconciliation_v1",
        "approver_id": "reconciliation_gate",
        "approved_at": now if status == "APPROVED" else None,
        "model_output": {
            "reconciliation": {
                "case_id": case.case_id,
                "run_id": case.run_id,
                "payment_id": case.payment_id,
                "classification": case.classification,
                "exception_codes": case.exception_codes,
                "explanation": case.explanation,
            },
            "calculation_trace": trace,
            "ai_interpretation": case.ai_interpretation,
            "ai_status": case.ai_status,
            "ai_invoked": case.ai_invoked,
            "ai_trigger_reason": case.ai_trigger_reason,
            "ai_technical_reason": case.ai_technical_reason,
            "ai_tool_calls": case.ai_tool_calls,
            "match_info": case.match_info,
            "source": "reconciliation",
        },
        "prev_decision_hash": prev_hash,
        "decision_hash": "",
        "created_at": now,
        "status": status,
    }
    decision_data["decision_hash"] = compute_decision_hash(decision_data, prev_hash)
    return decision_data


# ---------------------------------------------------------------------------
# Batch execution
# ---------------------------------------------------------------------------

def run_batch(
    tenant_id: str,
    records_data: list[dict],
    use_ai: bool = False,
    provider=None,
    source: str = "batch",
    force_ai: bool = False,
) -> ReconciliationRun:
    """Sync entry point for batch reconciliation (benchmark/CLI/tests)."""
    loop = _new_loop()
    try:
        return loop.run_until_complete(
            run_batch_async(tenant_id, records_data, use_ai=use_ai, provider=provider,
                            source=source, force_ai=force_ai)
        )
    finally:
        loop.close()


async def run_batch_async(
    tenant_id: str,
    records_data: list[dict],
    use_ai: bool = False,
    provider=None,
    source: str = "batch",
    db=None,
    force_ai: bool = False,
) -> ReconciliationRun:
    """Run a full batch reconciliation over the given records.

    Records are ingested idempotently, grouped by payment, reconciled
    individually, persisted, and mirrored into the decision ledger.

    Returns a ReconciliationRun with real, computed metrics.
    """
    run = ReconciliationRun(source=source)
    run_id = run.run_id
    start = time.time()

    # ── 1. Ingest (normalize + validate + dedup + persist) ──
    valid_records, ingest_errors, duplicate_count = await ingest_records_async(
        tenant_id, records_data, source=source, db=db,
    )
    run.errors = [
        f"Record {e.get('external_id', '?')}: {e.get('detail')}" for e in ingest_errors
    ]
    run.duplicates_detected = duplicate_count

    # Invalid records become first-class INVALID_RECORD exception cases so
    # they surface in the exception queue — never silently dropped.
    invalid_cases: list[ReconciliationCase] = []
    for err in ingest_errors:
        case = ReconciliationCase(payment_id=err.get("external_id", "unknown"))
        exc = invalid_record(
            err.get("external_id", "unknown"),
            err.get("detail", "record failed validation"),
            [],
        )
        case.classification = CLASS_EXCEPTION
        case.exception_codes = [exc.code]
        case.exceptions = [exc.to_dict()]
        case.explanation = f"Record rejected at ingestion: {err.get('detail')}"
        case.run_id = run_id
        invalid_cases.append(case)

    # ── 2. Group by payment (deterministic) ──
    payment_ids = group_payment_ids(valid_records)
    records_by_payment: dict[str, list[FinancialRecord]] = {}
    for rec in valid_records:
        key = rec.external_id if rec.record_type == RECORD_PAYMENT else rec.payment_id
        if not key:
            run.errors.append(f"Record {rec.external_id} has no payment reference")
            continue
        records_by_payment.setdefault(key, []).append(rec)

    # Duplicate webhook payloads are detected at run level (informational).
    payload_dups = detect_duplicate_payloads(valid_records)
    if payload_dups:
        logger.info(
            "Run %s: %d duplicate payload hash(es) detected (idempotent)",
            run_id, len(payload_dups),
        )

    # ── 3. Reconcile each payment ──
    cases: list[ReconciliationCase] = []
    case_latencies: list[int] = []
    for payment_id in payment_ids:
        case_start = time.time()
        records = records_by_payment.get(payment_id, [])
        order_id = ""
        for r in records:
            if r.record_type == RECORD_PAYMENT:
                order_id = r.order_id
                break
        case = reconcile_payment(
            tenant_id, payment_id, records,
            use_ai=use_ai, provider=provider, order_id=order_id,
            force_ai=force_ai,
        )
        case.run_id = run_id
        case_latencies.append(int((time.time() - case_start) * 1000))
        cases.append(case)

    # ── 4. Aggregate metrics (real, computed from actual cases) ──
    cases = invalid_cases + cases
    run.total_records = len(valid_records)
    run.total_cases = len(cases)
    run.matched = sum(1 for c in cases if c.classification == CLASS_MATCHED)
    run.review_required = sum(1 for c in cases if c.classification == CLASS_REVIEW_REQUIRED)
    run.exceptions = sum(1 for c in cases if c.classification == CLASS_EXCEPTION)
    run.match_rate = (run.matched / run.total_cases) if run.total_cases else 0.0
    if case_latencies:
        run.p50_latency_ms = statistics.median(case_latencies)
        sorted_lat = sorted(case_latencies)
        run.p95_latency_ms = sorted_lat[min(int(len(sorted_lat) * 0.95), len(sorted_lat) - 1)]
    duration_s = max(time.time() - start, 1e-9)
    run.throughput_per_sec = len(cases) / duration_s
    decided = 0
    for c in cases:
        captured = c.calculation_trace.get("captured_amount", 0) if c.calculation_trace else 0
        # Every case with a valid capture writes a ledger decision; cases
        # without one (missing payment / invalid record) are recorded as
        # first-class exceptions.  Either way the case is auditable.
        if captured > 0 or set(c.exception_codes) & {
            ExceptionCode.MISSING_PAYMENT.value,
            ExceptionCode.INVALID_RECORD.value,
        }:
            decided += 1
    run.audit_completeness = (decided / run.total_cases) if run.total_cases else 0.0

    run.status = "completed"
    run.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ── 5. Persist cases + ledger decisions (with final metrics) ──
    await _persist_run(tenant_id, run, cases, db)

    return run


async def _persist_run(tenant_id: str, run: ReconciliationRun, cases: list[ReconciliationCase], db=None) -> None:
    """Persist the run row, case rows, and ledger decisions."""
    from database import get_db

    own = db is None
    if own:
        db = await get_db()
    try:
        # Persist run
        await db.execute(
            "INSERT INTO reconciliation_runs "
            "(run_id, tenant_id, status, source, total_records, total_cases, matched, "
            " review_required, exceptions, match_rate, classification_accuracy, "
            " calculation_accuracy, false_auto_resolve, throughput_per_sec, "
            " p50_latency_ms, p95_latency_ms, duplicates_detected, audit_completeness, "
            " errors, started_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.run_id, tenant_id, run.status, run.source, run.total_records,
                run.total_cases, run.matched, run.review_required, run.exceptions,
                run.match_rate, run.classification_accuracy, run.calculation_accuracy,
                run.false_auto_resolve, run.throughput_per_sec, run.p50_latency_ms,
                run.p95_latency_ms, run.duplicates_detected, run.audit_completeness,
                json.dumps(run.errors), run.started_at, run.completed_at,
            ),
        )

        # Previous ledger hash: the TRUE chain tail is the decision whose
        # hash is not referenced as any other decision's prev_decision_hash.
        # Ordering by created_at alone is unreliable because seeded decisions
        # can share identical microsecond timestamps.
        prev_hash = "genesis"
        cursor = await db.execute(
            "SELECT d.decision_hash FROM decisions d "
            "WHERE d.tenant_id = ? AND d.decision_id != 'dec_005_tampered' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM decisions o "
            "  WHERE o.tenant_id = d.tenant_id "
            "    AND o.prev_decision_hash = d.decision_hash "
            "    AND o.decision_id != 'dec_005_tampered'"
            ") "
            "ORDER BY d.created_at DESC, d.decision_hash DESC LIMIT 1",
            (tenant_id,),
        )
        row = await cursor.fetchone()
        if row:
            prev_hash = row["decision_hash"]

        for case in cases:
            # Persist case — AI investigation metadata (invoked, trigger
            # reason, tool calls) is stored in first-class columns.
            tier_analysis = json.dumps({
                "tiers_applied": sorted(set(case.tiers_applied)),
                "tier_findings": case.tier_findings,
                "relationships": case.relationships,
            })
            await db.execute(
                "INSERT INTO reconciliation_cases "
                "(case_id, tenant_id, run_id, payment_id, related_record_ids, expected_amount, "
                " actual_amount, variance, classification, exception_codes, exceptions, "
                " ai_status, ai_invoked, ai_confidence, ai_interpretation, ai_technical_reason, "
                " ai_trigger_reason, ai_tool_calls, "
                " calculation_trace, match_info, tier_analysis, decision_id, explanation, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    case.case_id, tenant_id, case.run_id, case.payment_id,
                    json.dumps([r.record_id for r in case.records]),
                    case.expected_amount, case.actual_amount, case.variance,
                    case.classification, json.dumps(case.exception_codes),
                    json.dumps(case.exceptions), case.ai_status, case.ai_invoked,
                    case.ai_confidence, json.dumps(case.ai_interpretation),
                    case.ai_technical_reason, case.ai_trigger_reason, case.ai_tool_calls,
                    json.dumps(case.calculation_trace), json.dumps(case.match_info),
                    tier_analysis,
                    case.decision_id, case.explanation, case.created_at,
                ),
            )

            # Write ledger decision when a financial decision exists
            decision = _build_ledger_decision(case, prev_hash)
            if decision is None:
                continue
            await db.execute(
                "INSERT INTO decisions (decision_id, tenant_id, entity_type, entity_id, "
                " gross_amount, line_items, final_amount, policy_version_id, approver_id, "
                " approved_at, model_output, prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision["decision_id"], tenant_id, decision["entity_type"],
                    decision["entity_id"], decision["gross_amount"],
                    json.dumps(decision["line_items"]), decision["final_amount"],
                    decision["policy_version_id"], decision["approver_id"],
                    decision["approved_at"], json.dumps(decision["model_output"]),
                    decision["prev_decision_hash"], decision["decision_hash"],
                    decision["created_at"], decision["status"],
                ),
            )
            case.decision_id = decision["decision_id"]
            prev_hash = decision["decision_hash"]
            # Update case row with the decision id
            await db.execute(
                "UPDATE reconciliation_cases SET decision_id = ? WHERE case_id = ? AND tenant_id = ?",
                (decision["decision_id"], case.case_id, tenant_id),
            )

        await db.commit()
    finally:
        if own:
            await db.close()


# ---------------------------------------------------------------------------
# Razorpay → reconciliation records
# ---------------------------------------------------------------------------

async def records_from_razorpay_async(tenant_id: str, db=None) -> list[dict]:
    """Build normalized reconciliation records from stored Razorpay data.

    Deterministic linkage only — never guesses relationships:
    - payments from razorpay_payments (api_sync).
    - refunds from refund.processed webhook events (the money-moved state;
      refund.created / refund.failed do not represent money moved).
    - settlements from razorpay_settlements, linked to payments through the
      settlement recon table (GET /settlements/{id}/recon).  A settlement
      that cannot be linked is emitted UNLINKED (payment_id = its own id,
      extra.unlinked_settlement=True) and surfaces as an exception — never
      silently attached to the wrong payment.
    - fee/tax evidence derived from recon fee/tax fields when present.
    - Tier 5-7 context records (dispute / invoice / payment_link / downtime
      / account) from verified live webhook events.
    """
    from database import get_db

    own = db is None
    if own:
        db = await get_db()
    records: list[dict] = []
    try:
        # ── Payments (api sync) ──
        cur = await db.execute(
            "SELECT payment_id, order_id, amount, currency, status, captured, "
            "amount_refunded, raw_payload, first_seen_at "
            "FROM razorpay_payments WHERE tenant_id = ?",
            (tenant_id,),
        )
        payment_rows = await cur.fetchall()
        payment_order: dict[str, str] = {}  # payment_id -> order_id
        for row in payment_rows:
            payment_order[row["payment_id"]] = row["order_id"] or ""
            records.append({
                "record_type": RECORD_PAYMENT,
                "external_id": row["payment_id"],
                "amount": row["amount"],
                "currency": row["currency"],
                "status": "captured" if row["captured"] else row["status"],
                "payment_id": row["payment_id"],
                "order_id": row["order_id"] or "",
                "recorded_at": row["first_seen_at"],
                "source": "api_sync",
                "raw_evidence_ref": f"razorpay_payments:{row['payment_id']}",
                "payload_hash": "",
                "extra": {"amount_refunded": row["amount_refunded"]},
            })

        # ── Refunds: refund.processed only (money actually moved) ──
        cur = await db.execute(
            "SELECT event_id, raw_payload, payload_hash FROM razorpay_events "
            "WHERE tenant_id = ? AND event_type = 'refund.processed'",
            (tenant_id,),
        )
        for row in await cur.fetchall():
            payload = _parse_json(row["raw_payload"])
            refund = _entity_from_payload(payload, "refund")
            if not refund or not refund.get("id"):
                continue
            records.append({
                "record_type": RECORD_REFUND,
                "external_id": refund["id"],
                "amount": refund.get("amount", 0),
                "currency": refund.get("currency", "INR"),
                "status": refund.get("status", "processed"),
                "payment_id": refund.get("payment_id", ""),
                "order_id": "",
                "recorded_at": _ts_to_iso(refund.get("created_at")),
                "source": "live_webhook",
                "raw_evidence_ref": f"razorpay_events:{row['event_id']}",
                "payload_hash": row["payload_hash"] or "",
            })

        # ── Settlements: deterministic payment linkage via recon data ──
        # razorpay_settlement_recon rows are persisted during Razorpay sync
        # from GET /settlements/{id}/recon (order_id / payment_id / fee / tax).
        recon_links: dict[str, dict] = {}  # settlement_id -> {payment_id, order_id}
        recon_fee_tax: dict[str, dict] = {}  # payment_id -> {fee, tax}
        try:
            cur = await db.execute(
                "SELECT settlement_id, payment_id, order_id, fee, tax "
                "FROM razorpay_settlement_recon WHERE tenant_id = ? "
                "AND (payment_id != '' OR order_id != '')",
                (tenant_id,),
            )
            for rrow in await cur.fetchall():
                sid = rrow["settlement_id"]
                pid = rrow["payment_id"] or ""
                oid = rrow["order_id"] or ""
                # Reconcile payment-level rows; order-level rows resolve via
                # the payment's own order_id below.
                if pid:
                    recon_links.setdefault(sid, {"payment_id": pid, "order_id": oid})
                    if rrow["fee"] or rrow["tax"]:
                        prev = recon_fee_tax.setdefault(
                            pid, {"fee": 0, "tax": 0, "settlement_id": sid})
                        prev["fee"] = (prev.get("fee") or 0) + (rrow["fee"] or 0)
                        prev["tax"] = (prev.get("tax") or 0) + (rrow["tax"] or 0)
        except Exception:
            # Recon table not present (pre-0008 DB) — fall back to unlinked.
            logger.warning("razorpay_settlement_recon unavailable for tenant %s", tenant_id)

        # Order-level recon rows: settle a payment whose order matches.
        try:
            cur = await db.execute(
                "SELECT settlement_id, order_id, fee, tax FROM razorpay_settlement_recon "
                "WHERE tenant_id = ? AND payment_id = '' AND order_id != ''",
                (tenant_id,),
            )
            for rrow in await cur.fetchall():
                oid = rrow["order_id"]
                # Link an order-level recon row to a payment ONLY when exactly
                # one local payment belongs to that order.  Multiple payments
                # on one order make the mapping ambiguous — never guess.
                candidates = [
                    pid for pid, poid in payment_order.items()
                    if poid == oid and pid not in recon_fee_tax
                ]
                if len(candidates) == 1:
                    pid = candidates[0]
                    recon_links.setdefault(
                        rrow["settlement_id"], {"payment_id": pid, "order_id": oid})
                    if rrow["fee"] or rrow["tax"]:
                        recon_fee_tax[pid] = {
                            "fee": rrow["fee"] or 0, "tax": rrow["tax"] or 0,
                            "settlement_id": rrow["settlement_id"],
                        }
        except Exception:
            pass

        # Tier 4: deterministic fee/tax evidence from settlement recon.
        for pid, ft in recon_fee_tax.items():
            if (ft["fee"] or ft["tax"]) and pid in payment_order:
                records.append({
                    "record_type": RECORD_FEE_TAX,
                    "external_id": f"recon_{ft['settlement_id']}_{pid}",
                    "amount": (ft["fee"] or 0) + (ft["tax"] or 0),
                    "currency": "INR",
                    "status": "processed",
                    "payment_id": pid,
                    "order_id": payment_order.get(pid, ""),
                    "fee_amount": ft["fee"] or 0,
                    "tax_amount": ft["tax"] or 0,
                    "recorded_at": "",
                    "source": "settlement_recon",
                    "raw_evidence_ref": f"razorpay_settlement_recon:{ft['settlement_id']}",
                    "payload_hash": "",
                })

        # Settlements from API sync — linked through recon or unlinked.
        cur = await db.execute(
            "SELECT settlement_id, amount, currency, status, raw_payload, first_seen_at "
            "FROM razorpay_settlements WHERE tenant_id = ?",
            (tenant_id,),
        )
        for row in await cur.fetchall():
            sid = row["settlement_id"]
            link = recon_links.get(sid)
            if link and link.get("payment_id"):
                pid = link["payment_id"]
                records.append({
                    "record_type": RECORD_SETTLEMENT,
                    "external_id": sid,
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "status": row["status"],
                    "payment_id": pid,
                    "order_id": link.get("order_id", "") or payment_order.get(pid, ""),
                    "recorded_at": row["first_seen_at"],
                    "source": "api_sync",
                    "raw_evidence_ref": f"razorpay_settlements:{sid}",
                    "payload_hash": "",
                })
            else:
                # No deterministic link — preserve as evidence, route to review.
                records.append({
                    "record_type": RECORD_SETTLEMENT,
                    "external_id": sid,
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "status": row["status"],
                    "payment_id": sid,  # own id → unlinked group
                    "order_id": "",
                    "recorded_at": row["first_seen_at"],
                    "source": "api_sync",
                    "raw_evidence_ref": f"razorpay_settlements:{sid}",
                    "payload_hash": "",
                    "extra": {"unlinked_settlement": True},
                })

        # ── Settlement.processed webhook events (linked via payload when
        #    order/payment reference present, otherwise evidence-only).
        cur = await db.execute(
            "SELECT event_id, raw_payload, payload_hash FROM razorpay_events "
            "WHERE tenant_id = ? AND event_type = 'settlement.processed'",
            (tenant_id,),
        )
        seen_settlement_ids: set[str] = set()
        for row in await cur.fetchall():
            payload = _parse_json(row["raw_payload"])
            settlement = _entity_from_payload(payload, "settlement")
            if not settlement or not settlement.get("id"):
                continue
            sid = settlement["id"]
            if sid in seen_settlement_ids:
                continue  # same settlement delivered twice (idempotent)
            seen_settlement_ids.add(sid)
            pay_id = settlement.get("payment_id", "") or ""
            if not pay_id:
                # Webhook settlements may only reference order/recon; leave
                # unlinked rather than guessing.
                pay_id = sid
            records.append({
                "record_type": RECORD_SETTLEMENT,
                "external_id": sid,
                "amount": settlement.get("amount", 0),
                "currency": settlement.get("currency", "INR"),
                "status": settlement.get("status", "processed"),
                "payment_id": pay_id,
                "order_id": settlement.get("order_id", ""),
                "recorded_at": _ts_to_iso(settlement.get("settled_at") or settlement.get("created_at")),
                "source": "live_webhook",
                "raw_evidence_ref": f"razorpay_events:{row['event_id']}",
                "payload_hash": row["payload_hash"] or "",
                "extra": {"unlinked_settlement": pay_id == sid and not settlement.get("payment_id")},
            })

        # ── Tier 5-7 context records from verified live webhook events ──
        records.extend(await _context_records_from_events_async(tenant_id, db))

        return records
    finally:
        if own:
            await db.close()


async def _context_records_from_events_async(tenant_id: str, db) -> list[dict]:
    """Project Tier 5-7 context records from verified live webhook events.

    Context records (dispute / invoice / payment_link / operational) are
    evidence — the deterministic calculator never reads their amounts.  Only
    events whose source is a verified live webhook become context records so
    local simulator data is never presented as real Razorpay activity.
    """
    import razorpay_registry

    context_types = {
        "dispute", "invoice", "payment_link", "operational",
    }
    out: list[dict] = []
    try:
        cur = await db.execute(
            "SELECT event_id, event_type, source, verification_status, raw_payload, "
            "payload_hash FROM razorpay_events WHERE tenant_id = ? "
            "AND source = 'live_webhook' AND verification_status = 'verified'",
            (tenant_id,),
        )
        rows = await cur.fetchall()
    except Exception:
        return out

    for row in rows:
        event_type = row["event_type"]
        family = razorpay_registry.classify_event(event_type)["family"]
        rec_type = razorpay_registry.FAMILY_RECORD_TYPE.get(family)
        if rec_type not in context_types:
            continue
        payload = _parse_json(row["raw_payload"])
        entity, pay_id, order_id = _context_entity_ids(payload, family, event_type)
        if not entity:
            continue
        amount = entity.get("amount") or entity.get("amount_paid") or entity.get("amount_offered") or 0
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            amount = 0
        status = (entity.get("status") or "").lower()
        if family == razorpay_registry.FAMILY_DISPUTE and not status:
            status = "open" if razorpay_registry.dispute_event_is_open(event_type) else "closed"
        out.append({
            "record_type": rec_type,
            "external_id": entity.get("id") or f"{rec_type}_{row['event_id']}",
            "amount": amount,
            "currency": entity.get("currency", "INR"),
            "status": status or "unknown",
            "payment_id": pay_id,
            "order_id": order_id,
            "recorded_at": _ts_to_iso(entity.get("created_at")),
            "source": "live_webhook",
            "raw_evidence_ref": f"razorpay_events:{row['event_id']}",
            "payload_hash": row["payload_hash"] or "",
            "extra": {
                "family": family,
                "event_type": event_type,
                "entity_id": entity.get("id", ""),
            },
        })
    return out


def _context_entity_ids(payload: dict, family: str, event_type: str) -> tuple[dict, str, str]:
    """Extract (entity, payment_id, order_id) from a Razorpay webhook payload.

    Works from the actual payload structures Razorpay sends — the nested
    entity block plus any sibling payment/order block that Razorpay includes
    for related entities.  Never fabricates identifiers.
    """
    import razorpay_registry as reg

    payload_block = payload.get("payload", {})
    entity_key = {
        reg.FAMILY_DISPUTE: "dispute",
        reg.FAMILY_INVOICE: "invoice",
        reg.FAMILY_PAYMENT_LINK: "payment_link",
        reg.FAMILY_DOWNTIME: "downtime",
        reg.FAMILY_ACCOUNT: "account",
        reg.FAMILY_FUND_ACCOUNT: "fund_account",
        reg.FAMILY_ENGAGEMENT: "engage",
    }.get(family, family.lower())
    block = payload_block.get(entity_key, {}) if isinstance(payload_block, dict) else {}
    entity = block.get("entity", {}) if isinstance(block, dict) else {}
    if not isinstance(entity, dict):
        entity = {}

    pay_id = str(entity.get("payment_id") or "").strip()
    order_id = str(entity.get("order_id") or "").strip()

    # Sibling payment block provides the payment / order relationship when the
    # primary entity does not carry it directly.
    if (not pay_id or not order_id) and isinstance(payload_block, dict):
        pblock = payload_block.get("payment", {})
        if isinstance(pblock, dict):
            pent = pblock.get("entity", {})
            if isinstance(pent, dict):
                if not pay_id:
                    pay_id = str(pent.get("id") or "").strip()
                if not order_id:
                    order_id = str(pent.get("order_id") or "").strip()
        oblock = payload_block.get("order", {})
        if isinstance(oblock, dict):
            oent = oblock.get("entity", {})
            if isinstance(oent, dict) and not order_id:
                order_id = str(oent.get("id") or "").strip()
    return entity, pay_id, order_id


def records_from_razorpay(tenant_id: str) -> list[dict]:
    """Sync entry point for Razorpay → reconciliation records."""
    loop = _new_loop()
    try:
        return loop.run_until_complete(records_from_razorpay_async(tenant_id))
    finally:
        loop.close()


def _parse_json(val) -> dict:
    if isinstance(val, dict):
        return val
    try:
        return json.loads(val or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _entity_from_payload(payload: dict, entity_type: str) -> dict:
    """Extract <entity_type>.entity from a Razorpay webhook payload."""
    payload_block = payload.get("payload", {})
    block = payload_block.get(entity_type, {})
    if isinstance(block, dict):
        entity = block.get("entity", {})
        if isinstance(entity, dict):
            return entity
    return {}


def _ts_to_iso(ts) -> str:
    """Convert a Razorpay unix timestamp to ISO string (or empty)."""
    try:
        ts = int(ts)
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def _new_loop() -> asyncio.AbstractEventLoop:
    """Create a fresh event loop for sync entry points.

    The loop is intentionally NOT installed as the thread's current event
    loop: the sync wrappers close it afterwards, and leaving a closed loop
    behind would poison every later asyncio.get_event_loop() call in the
    same thread with 'Event loop is closed'.  Code running inside
    run_until_complete() reaches it via get_running_loop().
    """
    return asyncio.new_event_loop()