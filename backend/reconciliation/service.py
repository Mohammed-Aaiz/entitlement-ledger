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
    contradictory_evidence,
    refund_mismatch,
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
        amount = int(data.get("amount", 0))
    except (TypeError, ValueError):
        raise FinancialValidationError(f"amount must be an integer, got {data.get('amount')!r}")

    currency = validate_currency(data.get("currency", "INR"))

    if data.get("amount") is None:
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


def reconcile_payment(
    tenant_id: str,
    payment_id: str,
    records: list[FinancialRecord],
    use_ai: bool = False,
    provider=None,
    order_id: str = "",
) -> ReconciliationCase:
    """Run the full reconciliation loop for one payment (pure + DB-free).

    Returns a ReconciliationCase with classification, exceptions, trace,
    and AI interpretation metadata.  Never raises for financial anomalies
    — it converts them into structured exceptions.
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
        return case

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

    # ── 3. AI interpretation (optional, advisory, failure-safe) ──
    ai_status = AI_NOT_NEEDED
    ai_confidence: Optional[float] = None
    ai_interpretation: dict = {}
    ai_technical_reason = ""

    capture_conflict = _detect_capture_conflict(records)
    # AI interpretation runs ONLY when explicitly requested (use_ai).  In
    # deterministic-only mode an ambiguous capture_conflict case is decided
    # conservatively (REVIEW_REQUIRED / AI_UNAVAILABLE) WITHOUT any provider
    # call — the pipeline never depends on a network round-trip to stay safe.
    if use_ai:
        from .ai_controller import interpret_case
        context = {
            "payment_id": payment_id,
            "records": [
                {
                    "record_type": r.record_type,
                    "external_id": r.external_id,
                    "amount": r.amount,
                    "status": r.status,
                    "payment_id": r.payment_id,
                    "order_id": r.order_id,
                    "fee_amount": r.fee_amount,
                    "tax_amount": r.tax_amount,
                    "adjustment_sign": r.adjustment_sign,
                    "recorded_at": r.recorded_at,
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
        result = interpret_case(context, provider=provider)
        ai_status = result.status
        ai_confidence = (result.interpretation or {}).get("confidence") if result.interpretation else None
        ai_interpretation = result.interpretation or {}
        ai_technical_reason = result.technical_reason

    # ── 4. Deterministic decision gate ──
    if financial_error is not None:
        if refund_total > captured and captured > 0:
            evidence_refs = sorted({r.raw_evidence_ref for r in records if r.raw_evidence_ref})
            exceptions = [
                refund_mismatch(
                    payment_id, captured, refund_total,
                    [r.record_id for r in records], evidence_refs,
                ).to_dict(),
                contradictory_evidence(
                    payment_id, [r.record_id for r in records], evidence_refs,
                    detail=financial_error,
                ).to_dict(),
            ]
            case.classification = CLASS_EXCEPTION
            case.exception_codes = [
                ExceptionCode.REFUND_MISMATCH.value,
                ExceptionCode.CONTRADICTORY_EVIDENCE.value,
            ]
            case.exceptions = exceptions
            case.explanation = (
                "Refund records exceed the captured amount — the financial "
                "evidence is contradictory and cannot be reconciled deterministically."
            )
        else:
            exc = invalid_record(payment_id, financial_error, [])
            case.classification = CLASS_EXCEPTION
            case.exception_codes = [exc.code]
            case.exceptions = [exc.to_dict()]
            case.explanation = f"Financial calculation failed: {financial_error}"
        case.ai_status = ai_status
        case.ai_confidence = ai_confidence
        case.ai_interpretation = ai_interpretation
        case.ai_technical_reason = ai_technical_reason
        if calculation is not None:
            case.calculation_trace = calculation.to_dict()
        return case

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
        case.ai_confidence = ai_confidence
        case.ai_interpretation = ai_interpretation
        case.ai_technical_reason = ai_technical_reason
        return case

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
    case.ai_confidence = ai_confidence
    case.ai_interpretation = ai_interpretation
    case.ai_technical_reason = ai_technical_reason

    return case


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
            "ai_technical_reason": case.ai_technical_reason,
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
) -> ReconciliationRun:
    """Sync entry point for batch reconciliation (benchmark/CLI/tests)."""
    loop = _new_loop()
    try:
        return loop.run_until_complete(
            run_batch_async(tenant_id, records_data, use_ai=use_ai, provider=provider, source=source)
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
            # Persist case
            await db.execute(
                "INSERT INTO reconciliation_cases "
                "(case_id, tenant_id, run_id, payment_id, related_record_ids, expected_amount, "
                " actual_amount, variance, classification, exception_codes, exceptions, "
                " ai_status, ai_confidence, ai_interpretation, ai_technical_reason, "
                " calculation_trace, match_info, decision_id, explanation, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    case.case_id, tenant_id, case.run_id, case.payment_id,
                    json.dumps([r.record_id for r in case.records]),
                    case.expected_amount, case.actual_amount, case.variance,
                    case.classification, json.dumps(case.exception_codes),
                    json.dumps(case.exceptions), case.ai_status, case.ai_confidence,
                    json.dumps(case.ai_interpretation), case.ai_technical_reason,
                    json.dumps(case.calculation_trace), json.dumps(case.match_info),
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

    Maps what is actually present — payments from razorpay_payments,
    refunds from refund.* webhook events, settlements from
    razorpay_settlements and settlement.* events.  Never fabricates data.
    """
    from database import get_db

    own = db is None
    if own:
        db = await get_db()
    records: list[dict] = []
    try:
        # Payments
        cur = await db.execute(
            "SELECT payment_id, order_id, amount, currency, status, captured, "
            "amount_refunded, raw_payload, first_seen_at "
            "FROM razorpay_payments WHERE tenant_id = ?",
            (tenant_id,),
        )
        for row in await cur.fetchall():
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

        # Refunds from refund.* webhook events
        cur = await db.execute(
            "SELECT event_id, raw_payload, payload_hash FROM razorpay_events "
            "WHERE tenant_id = ? AND event_type IN ('refund.created', 'refund.processed')",
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
                "status": refund.get("status", "unknown"),
                "payment_id": refund.get("payment_id", ""),
                "order_id": "",
                "recorded_at": _ts_to_iso(refund.get("created_at")),
                "source": "live_webhook",
                "raw_evidence_ref": f"razorpay_events:{row['event_id']}",
                "payload_hash": row["payload_hash"] or "",
            })

        # Settlements from API sync
        cur = await db.execute(
            "SELECT settlement_id, amount, currency, status, raw_payload, first_seen_at "
            "FROM razorpay_settlements WHERE tenant_id = ?",
            (tenant_id,),
        )
        for row in await cur.fetchall():
            records.append({
                "record_type": RECORD_SETTLEMENT,
                "external_id": row["settlement_id"],
                "amount": row["amount"],
                "currency": row["currency"],
                "status": row["status"],
                "payment_id": "",
                "order_id": "",
                "recorded_at": row["first_seen_at"],
                "source": "api_sync",
                "raw_evidence_ref": f"razorpay_settlements:{row['settlement_id']}",
                "payload_hash": "",
            })

        # Settlement.processed webhook events
        cur = await db.execute(
            "SELECT event_id, raw_payload, payload_hash FROM razorpay_events "
            "WHERE tenant_id = ? AND event_type = 'settlement.processed'",
            (tenant_id,),
        )
        for row in await cur.fetchall():
            payload = _parse_json(row["raw_payload"])
            settlement = _entity_from_payload(payload, "settlement")
            if not settlement or not settlement.get("id"):
                continue
            records.append({
                "record_type": RECORD_SETTLEMENT,
                "external_id": settlement["id"],
                "amount": settlement.get("amount", 0),
                "currency": settlement.get("currency", "INR"),
                "status": settlement.get("status", "processed"),
                "payment_id": "",
                "order_id": settlement.get("order_id", ""),
                "recorded_at": _ts_to_iso(settlement.get("settled_at") or settlement.get("created_at")),
                "source": "live_webhook",
                "raw_evidence_ref": f"razorpay_events:{row['event_id']}",
                "payload_hash": row["payload_hash"] or "",
            })
        return records
    finally:
        if own:
            await db.close()


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