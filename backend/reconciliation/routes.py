"""Reconciliation API routes.

Endpoints:
    POST /api/reconciliation/run              — run batch reconciliation
    GET  /api/reconciliation/runs             — list runs
    GET  /api/reconciliation/runs/{run_id}    — run detail
    GET  /api/reconciliation/runs/{run_id}/exceptions — exception cases
    GET  /api/reconciliation/cases/{case_id}  — full case detail
    GET  /api/reconciliation/dashboard        — finance control room stats
    POST /api/reconciliation/run/razorpay     — reconcile stored Razorpay data

All endpoints require authentication and are tenant-scoped.
"""
from __future__ import annotations

import json
import logging

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header

from auth import CurrentUser, get_current_user
from database import get_db, log_audit

from .schemas import (
    ReconciliationRunRequest,
    ReconciliationRunResponse,
    ReconciliationCaseResponse,
    ReconciliationDashboard,
)
from .service import run_batch_async, records_from_razorpay_async

logger = logging.getLogger(__name__)

router = APIRouter()


async def _existing_run_for_key(tenant_id: str, idempotency_key: str) -> Optional[dict]:
    """Return the previously-created run for an idempotency key, if any."""
    if not idempotency_key:
        return None
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT r.* FROM reconciliation_runs r "
            "JOIN reconciliation_run_idempotency i ON i.run_id = r.run_id "
            "WHERE i.tenant_id = ? AND i.idempotency_key = ?",
            (tenant_id, idempotency_key),
        )
        row = await cursor.fetchone()
        return _run_to_response(row) if row else None
    finally:
        await db.close()


async def _record_idempotent_run(tenant_id: str, idempotency_key: str, run_id: str) -> None:
    """Persist (tenant, key) → run for safe client retries.

    Retrying a timed-out POST with the SAME key returns the original run
    instead of creating duplicate runs/cases/ledger decisions.  A concurrent
    duplicate insert is harmless — the run itself is idempotently persisted.
    """
    if not idempotency_key:
        return
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO reconciliation_run_idempotency "
            "(tenant_id, idempotency_key, run_id) VALUES (?, ?, ?)",
            (tenant_id, idempotency_key, run_id),
        )
        await db.commit()
    finally:
        await db.close()


def _parse_json_field(val):
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val if val else {}
    return val if val else {}


def _run_to_response(row) -> dict:
    return {
        "run_id": row["run_id"],
        "status": row["status"],
        "source": row["source"],
        "total_records": row["total_records"],
        "total_cases": row["total_cases"],
        "matched": row["matched"],
        "review_required": row["review_required"],
        "exceptions": row["exceptions"],
        "match_rate": row["match_rate"],
        "classification_accuracy": row["classification_accuracy"],
        "calculation_accuracy": row["calculation_accuracy"],
        "false_auto_resolve": row["false_auto_resolve"],
        "throughput_per_sec": row["throughput_per_sec"],
        "p50_latency_ms": row["p50_latency_ms"],
        "p95_latency_ms": row["p95_latency_ms"],
        "duplicates_detected": row["duplicates_detected"],
        "audit_completeness": row["audit_completeness"],
        "errors": _parse_json_field(row["errors"]) if "errors" in row.keys() else [],
        "started_at": row["started_at"] or "",
        "completed_at": row["completed_at"] or "",
    }


def _decision_row_to_dict(row) -> dict:
    """Convert a decisions row to the dict shape hash verification expects
    (JSON fields parsed — hashes are computed over parsed structures)."""
    return {
        "decision_id": row["decision_id"],
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "gross_amount": row["gross_amount"],
        "line_items": _parse_json_field(row["line_items"]),
        "final_amount": row["final_amount"],
        "policy_version_id": row["policy_version_id"],
        "approver_id": row["approver_id"],
        "approved_at": row["approved_at"],
        "model_output": _parse_json_field(row["model_output"]),
        "prev_decision_hash": row["prev_decision_hash"],
        "decision_hash": row["decision_hash"],
        "created_at": row["created_at"],
        "status": row["status"],
    }


def _case_to_response(row) -> dict:
    _ta = _parse_json_field(row["tier_analysis"]) if "tier_analysis" in row.keys() else {}
    return {
        "case_id": row["case_id"],
        "payment_id": row["payment_id"],
        "run_id": row["run_id"],
        "classification": row["classification"],
        "expected_amount": row["expected_amount"],
        "actual_amount": row["actual_amount"],
        "variance": row["variance"],
        "exception_codes": _parse_json_field(row["exception_codes"]),
        "exceptions": _parse_json_field(row["exceptions"]),
        "ai_status": row["ai_status"],
        "ai_invoked": bool(row["ai_invoked"]),
        "ai_confidence": row["ai_confidence"],
        "ai_interpretation": _parse_json_field(row["ai_interpretation"]),
        "ai_technical_reason": row["ai_technical_reason"] or "",
        "ai_trigger_reason": row["ai_trigger_reason"] or "",
        "ai_tool_calls": int(row["ai_tool_calls"] or 0),
        "calculation_trace": _parse_json_field(row["calculation_trace"]),
        "match_info": _parse_json_field(row["match_info"]),
        "decision_id": row["decision_id"] or "",
        "explanation": row["explanation"] or "",
        "related_record_ids": _parse_json_field(row["related_record_ids"]),
        # Tier 1-7 analysis + relationship graph (persisted per case).
        "tier_findings": _ta.get("tier_findings", []),
        "tiers_applied": _ta.get("tiers_applied", []),
        "relationships": _ta.get("relationships", []),
        "created_at": row["created_at"] or "",
    }


@router.post("/reconciliation/run", response_model=ReconciliationRunResponse)
async def run_reconciliation(
    req: ReconciliationRunRequest,
    user: CurrentUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Run a batch reconciliation over the submitted records.

    Safe to retry: submitting the SAME Idempotency-Key returns the original
    run instead of creating a duplicate run/cases/ledger decisions.
    """
    if not req.records:
        raise HTTPException(400, "records must contain at least one record")

    # Client retry of a timed-out POST returns the ORIGINAL run — never a
    # second execution of the same financial work.
    existing = await _existing_run_for_key(user.tenant_id, idempotency_key or "")
    if existing:
        logger.info("Idempotent replay for key %s -> run %s", idempotency_key, existing["run_id"])
        return ReconciliationRunResponse(**existing)

    run = await run_batch_async(
        tenant_id=user.tenant_id,
        records_data=[r.model_dump() for r in req.records],
        use_ai=req.use_ai,
        source=req.source,
    )
    await _record_idempotent_run(user.tenant_id, idempotency_key or "", run.run_id)

    await log_audit(user.tenant_id, "reconciliation.run", "reconciliation_run", run.run_id,
                    user_id=user.user_id,
                    details={"records": run.total_records, "cases": run.total_cases,
                             "matched": run.matched, "review": run.review_required,
                             "exceptions": run.exceptions})

    return ReconciliationRunResponse(**run.to_dict())


@router.post("/reconciliation/run/razorpay", response_model=ReconciliationRunResponse)
async def run_razorpay_reconciliation(
    use_ai: bool = False,
    user: CurrentUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Reconcile the stored Razorpay payments/refunds/settlements for this tenant."""
    records = await records_from_razorpay_async(user.tenant_id)
    if not records:
        raise HTTPException(
            422,
            "No Razorpay data found. Sync Razorpay data first (POST /api/razorpay/sync/{orders|payments|settlements}) "
            "or submit records directly to /api/reconciliation/run.",
        )

    existing = await _existing_run_for_key(user.tenant_id, idempotency_key or "")
    if existing:
        return ReconciliationRunResponse(**existing)

    run = await run_batch_async(
        tenant_id=user.tenant_id,
        records_data=records,
        use_ai=use_ai,
        source="razorpay",
    )
    await _record_idempotent_run(user.tenant_id, idempotency_key or "", run.run_id)

    await log_audit(user.tenant_id, "reconciliation.run.razorpay", "reconciliation_run", run.run_id,
                    user_id=user.user_id, details={"records": run.total_records})

    return ReconciliationRunResponse(**run.to_dict())


@router.post("/reconciliation/run/demo", response_model=ReconciliationRunResponse)
async def run_demo_reconciliation(
    count: int = 100,
    user: CurrentUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Run reconciliation over the deterministic 100-record demo dataset.

    Uses the SAME real pipeline as production — only the input is synthetic
    (the ground-truth finance dataset).  Ground truth is never exposed.
    """
    count = min(max(count, 10), 100)
    from .dataset import generate_dataset, records_for_inference
    from .service import run_batch_async

    cases = generate_dataset(count=count, seed=42)
    records = []
    for case in cases:
        records.extend(records_for_inference(case))

    existing = await _existing_run_for_key(user.tenant_id, idempotency_key or "")
    if existing:
        return ReconciliationRunResponse(**existing)

    run = await run_batch_async(
        tenant_id=user.tenant_id,
        records_data=records,
        use_ai=False,
        source="demo_100",
    )
    await _record_idempotent_run(user.tenant_id, idempotency_key or "", run.run_id)

    await log_audit(user.tenant_id, "reconciliation.run.demo", "reconciliation_run", run.run_id,
                    user_id=user.user_id, details={"records": run.total_records, "cases": run.total_cases})

    return ReconciliationRunResponse(**run.to_dict())


@router.get("/reconciliation/runs")
async def list_reconciliation_runs(
    limit: int = 20,
    user: CurrentUser = Depends(get_current_user),
):
    """List reconciliation runs (most recent first)."""
    limit = min(max(limit, 1), 100)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM reconciliation_runs WHERE tenant_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (user.tenant_id, limit),
        )
        rows = await cursor.fetchall()
        return {"runs": [_run_to_response(r) for r in rows], "total": len(rows)}
    finally:
        await db.close()


@router.get("/reconciliation/runs/{run_id}", response_model=ReconciliationRunResponse)
async def get_reconciliation_run(run_id: str, user: CurrentUser = Depends(get_current_user)):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM reconciliation_runs WHERE run_id = ? AND tenant_id = ?",
            (run_id, user.tenant_id),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Reconciliation run {run_id} not found")
        return ReconciliationRunResponse(**_run_to_response(row))
    finally:
        await db.close()


@router.get("/reconciliation/runs/{run_id}/exceptions")
async def get_run_exceptions(run_id: str, user: CurrentUser = Depends(get_current_user)):
    """List every unresolved/exception case for a run, individually."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM reconciliation_runs WHERE run_id = ? AND tenant_id = ?",
            (run_id, user.tenant_id),
        )
        if not await cursor.fetchone():
            raise HTTPException(404, f"Reconciliation run {run_id} not found")

        cursor = await db.execute(
            "SELECT * FROM reconciliation_cases WHERE run_id = ? AND tenant_id = ? "
            "AND classification != 'MATCHED' ORDER BY payment_id",
            (run_id, user.tenant_id),
        )
        rows = await cursor.fetchall()
        return {"exceptions": [_case_to_response(r) for r in rows], "total": len(rows)}
    finally:
        await db.close()


@router.get("/reconciliation/cases/{case_id}", response_model=ReconciliationCaseResponse)
async def get_reconciliation_case(case_id: str, user: CurrentUser = Depends(get_current_user)):
    """Full detail for one reconciliation case (trace, exceptions, decision link)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM reconciliation_cases WHERE case_id = ? AND tenant_id = ?",
            (case_id, user.tenant_id),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Reconciliation case {case_id} not found")
        return ReconciliationCaseResponse(**_case_to_response(row))
    finally:
        await db.close()


@router.get("/reconciliation/dashboard", response_model=ReconciliationDashboard)
async def reconciliation_dashboard(user: CurrentUser = Depends(get_current_user)):
    """Aggregate stats for the Finance Control Room."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM reconciliation_runs WHERE tenant_id = ?",
            (user.tenant_id,),
        )
        total_runs = (await cursor.fetchone())["cnt"]

        latest_run = None
        if total_runs > 0:
            cursor = await db.execute(
                "SELECT * FROM reconciliation_runs WHERE tenant_id = ? "
                "ORDER BY started_at DESC LIMIT 1",
                (user.tenant_id,),
            )
            latest_row = await cursor.fetchone()
            if latest_row:
                latest_run = _run_to_response(latest_row)

        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM reconciliation_cases WHERE tenant_id = ?",
            (user.tenant_id,),
        )
        total_cases = (await cursor.fetchone())["cnt"]

        cursor = await db.execute(
            "SELECT classification, COUNT(*) as cnt FROM reconciliation_cases "
            "WHERE tenant_id = ? GROUP BY classification",
            (user.tenant_id,),
        )
        counts = {row["classification"]: row["cnt"] for row in await cursor.fetchall()}
        matched = counts.get("MATCHED", 0)
        review = counts.get("REVIEW_REQUIRED", 0)
        exceptions = counts.get("EXCEPTION", 0)

        cursor = await db.execute(
            "SELECT COALESCE(SUM(variance), 0) as v FROM reconciliation_cases "
            "WHERE tenant_id = ?",
            (user.tenant_id,),
        )
        total_variance = (await cursor.fetchone())["v"]

        cursor = await db.execute(
            "SELECT * FROM reconciliation_cases WHERE tenant_id = ? "
            "AND classification != 'MATCHED' ORDER BY created_at DESC LIMIT 50",
            (user.tenant_id,),
        )
        unresolved = [_case_to_response(r) for r in await cursor.fetchall()]

        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM decisions WHERE tenant_id = ? AND entity_type = 'reconciliation'",
            (user.tenant_id,),
        )
        rec_decisions = (await cursor.fetchone())["cnt"]

        # ── Tier distribution + AI usage — real values from case rows ──
        cursor = await db.execute(
            "SELECT ai_invoked, tier_analysis FROM reconciliation_cases WHERE tenant_id = ?",
            (user.tenant_id,),
        )
        tier_rows = await cursor.fetchall()
        tier_counts: dict = {}
        ai_invoked_cases = 0
        for trow in tier_rows:
            if trow["ai_invoked"]:
                ai_invoked_cases += 1
            tinfo = _parse_json_field(trow["tier_analysis"])
            for tier in (tinfo or {}).get("tiers_applied", []):
                tier_counts[str(tier)] = tier_counts.get(str(tier), 0) + 1
        total_with_tiers = sum(tier_counts.values())
        # Sort tiers numerically in the response.
        tier_counts = {k: tier_counts[k] for k in sorted(tier_counts, key=int)}
        ai_invocation_rate = (ai_invoked_cases / total_cases) if total_cases else 0.0
        deterministic_only_rate = (
            (total_cases - ai_invoked_cases) / total_cases
        ) if total_cases else 1.0

        # ── Real ledger verification — NEVER hard-coded. ──
        # Same convention as /api/decisions/verify-all: the seeded
        # dec_005_tampered record is the intentional tamper-demo, excluded
        # from chain verification here (it is verified individually via
        # /api/decisions/dec_005_tampered/verify).
        from hash_chain import verify_chain_by_links

        cursor = await db.execute(
            "SELECT decision_id, entity_type, entity_id, gross_amount, line_items, final_amount, "
            "policy_version_id, approver_id, approved_at, model_output, prev_decision_hash, "
            "decision_hash, created_at, status "
            "FROM decisions WHERE tenant_id = ? AND decision_id != 'dec_005_tampered'",
            (user.tenant_id,),
        )
        decision_rows = await cursor.fetchall()
        ledger_check = verify_chain_by_links(
            [_decision_row_to_dict(r) for r in decision_rows]
        )

        return ReconciliationDashboard(
            total_runs=total_runs,
            latest_run=latest_run,
            total_cases=total_cases,
            matched=matched,
            review_required=review,
            exceptions=exceptions,
            match_rate=(matched / total_cases) if total_cases else 0.0,
            total_variance=total_variance,
            unresolved_exceptions=unresolved,
            false_auto_resolve_risk_cases=[
                c for c in unresolved if c["classification"] == "EXCEPTION"
            ],
            ledger_verified=bool(ledger_check["valid"]),
            ledger_check={
                "checked_count": ledger_check["checked_count"],
                "chains": ledger_check["chains"],
                "heads": ledger_check["heads"],
                "issues": ledger_check["issues"],
                "break_at": ledger_check["break_at"],
            },
            tier_counts=tier_counts,
            ai_invoked_cases=ai_invoked_cases,
            ai_invocation_rate=ai_invocation_rate,
            deterministic_only_rate=deterministic_only_rate,
        )
    finally:
        await db.close()