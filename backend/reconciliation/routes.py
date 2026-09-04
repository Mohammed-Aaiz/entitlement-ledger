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

from fastapi import APIRouter, Depends, HTTPException

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


def _case_to_response(row) -> dict:
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
        "ai_confidence": row["ai_confidence"],
        "ai_interpretation": _parse_json_field(row["ai_interpretation"]),
        "ai_technical_reason": row["ai_technical_reason"] or "",
        "calculation_trace": _parse_json_field(row["calculation_trace"]),
        "match_info": _parse_json_field(row["match_info"]),
        "decision_id": row["decision_id"] or "",
        "explanation": row["explanation"] or "",
        "related_record_ids": _parse_json_field(row["related_record_ids"]),
        "created_at": row["created_at"] or "",
    }


@router.post("/reconciliation/run", response_model=ReconciliationRunResponse)
async def run_reconciliation(
    req: ReconciliationRunRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Run a batch reconciliation over the submitted records."""
    if not req.records:
        raise HTTPException(400, "records must contain at least one record")

    run = await run_batch_async(
        tenant_id=user.tenant_id,
        records_data=[r.model_dump() for r in req.records],
        use_ai=req.use_ai,
        source=req.source,
    )

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
):
    """Reconcile the stored Razorpay payments/refunds/settlements for this tenant."""
    records = await records_from_razorpay_async(user.tenant_id)
    if not records:
        raise HTTPException(
            422,
            "No Razorpay data found. Sync Razorpay data first (POST /api/razorpay/sync/{orders|payments|settlements}) "
            "or submit records directly to /api/reconciliation/run.",
        )

    run = await run_batch_async(
        tenant_id=user.tenant_id,
        records_data=records,
        use_ai=use_ai,
        source="razorpay",
    )

    await log_audit(user.tenant_id, "reconciliation.run.razorpay", "reconciliation_run", run.run_id,
                    user_id=user.user_id, details={"records": run.total_records})

    return ReconciliationRunResponse(**run.to_dict())


@router.post("/reconciliation/run/demo", response_model=ReconciliationRunResponse)
async def run_demo_reconciliation(
    count: int = 100,
    user: CurrentUser = Depends(get_current_user),
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

    run = await run_batch_async(
        tenant_id=user.tenant_id,
        records_data=records,
        use_ai=False,
        source="demo_100",
    )

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
            ledger_verified=True,
        )
    finally:
        await db.close()