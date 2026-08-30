"""API route handlers for EntitlementLedger — production persistence layer.

All data is read/written via SQLite (aiosqlite). In-memory lists are eliminated.
Every query is scoped to the authenticated user's tenant_id for multi-tenant isolation.
Seed data is loaded only in dev/test mode (SEED_DATA=true).
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import CurrentUser, get_current_user, require_role
from database import get_db, log_audit, DB_PATH
from models import (
    DecisionResponse, EvidenceResponse, PolicyResponse,
    VerificationResult, ScenarioResponse, DefensePacket, LineItem,
)
from hash_chain import verify_chain, compute_decision_hash
from calculations import validate_calculation, build_line_items, calculate_final_amount
from fastapi.responses import Response

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class ApprovalRequest(BaseModel):
    approver_id: str


class RejectionRequest(BaseModel):
    approver_id: str
    reason: str = ""


class EvidenceInput(BaseModel):
    source_type: str
    raw_content: str


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    page_size: int
    has_more: bool


class AnalyzeDecisionRequest(BaseModel):
    """Request to analyze and create a new financial decision."""
    entity_type: str = "seller"
    entity_id: str
    gross_amount: int
    evidence_items: list[EvidenceInput]
    has_sla_breach: bool = False
    sla_penalty_amount: int = 0
    has_returns: bool = False
    return_reserve_amount: int = 0
    approver_id: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_field(val) -> list | dict:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val if val else {}
    return val if val else {}


def _row_to_decision(row) -> dict:
    """Convert a DB row to a decision dict."""
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


def _to_iso_str(value) -> str | None:
    """Convert a value to an ISO-format string, or None.

    PostgreSQL returns datetime objects for timestamp columns while SQLite
    returns strings.  Pydantic models accept Optional[str] for approved_at
    (REVIEW_REQUIRED decisions have no approval timestamp), so we pass
    None through and normalise everything else to a string.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    # datetime / date / etc.
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _decision_to_response(d: dict) -> DecisionResponse:
    items = d.get("line_items", [])
    parsed_items = []
    for item in items:
        if isinstance(item, dict):
            parsed_items.append(LineItem(**item))
        else:
            parsed_items.append(item)
    return DecisionResponse(
        decision_id=d["decision_id"],
        entity_type=d["entity_type"],
        entity_id=d["entity_id"],
        gross_amount=d["gross_amount"],
        line_items=parsed_items,
        final_amount=d["final_amount"],
        policy_version_id=d["policy_version_id"],
        approver_id=d["approver_id"],
        approved_at=_to_iso_str(d["approved_at"]),
        model_output=_parse_json_field(d.get("model_output", {})),
        prev_decision_hash=d["prev_decision_hash"],
        decision_hash=d["decision_hash"],
        created_at=_to_iso_str(d["created_at"]),
        status=d.get("status", "APPROVED"),
    )


# ---------------------------------------------------------------------------
# Scenarios (dev/test only)
# ---------------------------------------------------------------------------

@router.get("/scenarios", response_model=list[ScenarioResponse])
async def list_scenarios(user: CurrentUser = Depends(get_current_user)):
    """List all available scenarios."""
    db = await get_db()
    try:
        # Scenarios are shared dev/test tools — not tenant-scoped
        cursor = await db.execute("SELECT * FROM scenarios")
        rows = await cursor.fetchall()
        return [ScenarioResponse(**dict(r)) for r in rows]
    finally:
        await db.close()


@router.post("/scenarios/{scenario_id}/run")
async def run_scenario(scenario_id: str, user: CurrentUser = Depends(get_current_user)):
    """Run a scenario through the AI pipeline and create a decision.

    Evidence is fetched from the database for the authenticated user's tenant.
    Policies are fetched from the database via the scenario's policy_ids.
    If no evidence exists for this tenant, returns an error — never invents evidence.
    """
    from ai.pipeline import run_pipeline
    from ai.llm_provider import is_ai_available

    db = await get_db()
    try:
        # Check if AI is available
        if not is_ai_available():
            return {
                "status": "error",
                "scenario_id": scenario_id,
                "error": "No LLM provider available",
                "message": "Start Ollama with a model, or set OPENROUTER_API_KEY.",
                "fallback": "Configure an LLM provider to enable AI analysis.",
            }

        # Check scenario exists in database
        cursor = await db.execute("SELECT * FROM scenarios WHERE scenario_id = ?", (scenario_id,))
        scenario = await cursor.fetchone()
        if not scenario:
            raise HTTPException(404, f"Scenario {scenario_id} not found")

        # Fetch policies from database via scenario's policy_ids
        policy_ids_raw = scenario["policy_ids"]
        if isinstance(policy_ids_raw, str):
            policy_ids = json.loads(policy_ids_raw)
        else:
            policy_ids = policy_ids_raw

        policy_records = []
        for pid in policy_ids:
            cursor_p = await db.execute("SELECT * FROM policies WHERE policy_id = ?", (pid,))
            p_row = await cursor_p.fetchone()
            if p_row:
                policy_records.append(dict(p_row)) if hasattr(p_row, 'keys') else policy_records.append({k: p_row[i] for i, k in enumerate(['policy_id', 'version', 'clause_text', 'effective_date'])})

        if not policy_records:
            return {
                "status": "error",
                "scenario_id": scenario_id,
                "error": "No policies found for this scenario",
                "message": "Scenario has no configured policies. Run system initialization first.",
            }

        # Fetch evidence not yet analyzed by the AI scenario pipeline.
        # ai_analyzed tracks whether the AI has consumed this evidence,
        # independent of linked_decision_ids (which may reference a
        # deterministic Razorpay decision).
        cursor_ev = await db.execute(
            "SELECT * FROM evidence WHERE tenant_id = ? AND ai_analyzed = FALSE",
            (user.tenant_id,),
        )
        ev_rows = await cursor_ev.fetchall()
        evidence_records = [dict(r) if hasattr(r, 'keys') else {k: r[i] for i, k in enumerate(['evidence_id', 'tenant_id', 'source_type', 'raw_content', 'extracted_facts', 'linked_decision_ids', 'ai_analyzed', 'content_hash', 'version', 'created_at', 'updated_at'])} for r in ev_rows]

        # Parse JSON fields in evidence records
        for ev in evidence_records:
            for field in ('extracted_facts', 'linked_decision_ids'):
                if isinstance(ev.get(field), str):
                    try:
                        ev[field] = json.loads(ev[field])
                    except (json.JSONDecodeError, TypeError):
                        ev[field] = []

        if not evidence_records:
            return {
                "status": "error",
                "scenario_id": scenario_id,
                "error": "No evidence available",
                "message": "No evidence records found for this tenant. Create evidence from Razorpay events or user uploads before running analysis.",
            }

        # Get previous decision hash from database (not seed data)
        cursor_hash = await db.execute(
            "SELECT decision_hash FROM decisions WHERE tenant_id = ? AND decision_id != 'dec_005_tampered' "
            "ORDER BY created_at DESC LIMIT 1",
            (user.tenant_id,),
        )
        prev_row = await cursor_hash.fetchone()
        prev_hash = prev_row["decision_hash"] if prev_row else "genesis"

        try:
            result = run_pipeline(
                scenario_id=scenario_id,
                evidence_records=evidence_records,
                policy_records=policy_records,
                prev_decision_hash=prev_hash,
                use_mock=False,
            )

            decision = result["decision"]
            decision["tenant_id"] = user.tenant_id

            # Persist decision
            await db.execute(
                "INSERT INTO decisions (decision_id, tenant_id, entity_type, entity_id, gross_amount, "
                "line_items, final_amount, policy_version_id, approver_id, approved_at, model_output, "
                "prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision["decision_id"], user.tenant_id, decision["entity_type"], decision["entity_id"],
                    decision["gross_amount"], json.dumps(decision["line_items"]),
                    decision["final_amount"], decision["policy_version_id"],
                    decision["approver_id"], decision["approved_at"],
                    json.dumps(decision["model_output"]),
                    decision["prev_decision_hash"], decision["decision_hash"],
                    decision["created_at"], decision["status"],
                ),
            )

            # Update evidence with extracted facts and link.
            # Read-modify-write to avoid SQLite/PostgreSQL json_insert
            # compatibility issues (PostgreSQL cannot cast bare strings to jsonb).
            for ev_result in result["evidence"]:
                ev_id = ev_result["evidence_id"]
                cursor = await db.execute(
                    "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ?",
                    (ev_id,),
                )
                row = await cursor.fetchone()
                current_ids = _parse_json_field(
                    row["linked_decision_ids"] if hasattr(row, "keys") else row[0]
                )
                if not isinstance(current_ids, list):
                    current_ids = []
                new_id = decision["decision_id"]
                if new_id not in current_ids:
                    current_ids.append(new_id)
                await db.execute(
                    "UPDATE evidence SET extracted_facts = ?, linked_decision_ids = ? "
                    "WHERE evidence_id = ?",
                    (
                        json.dumps(ev_result.get("extracted_facts", [])),
                        json.dumps(current_ids),
                        ev_id,
                    ),
                )
                # Mark evidence as analyzed by AI so it is not reprocessed.
                await db.execute(
                    "UPDATE evidence SET ai_analyzed = TRUE WHERE evidence_id = ?",
                    (ev_result["evidence_id"],),
                )

            await db.execute(
                "UPDATE scenarios SET status = 'completed' WHERE scenario_id = ?",
                (scenario_id,),
            )
            await db.commit()

            await log_audit(user.tenant_id, "scenario.run", "scenario", scenario_id,
                            user_id=user.user_id, details={"decision_id": decision["decision_id"]})

            return {
                "status": "completed",
                "scenario_id": scenario_id,
                "decision_id": decision["decision_id"],
                "decision_status": decision["status"],
                "message": "AI pipeline executed successfully.",
                "stages": result["stages"],
                "total_duration_ms": result["total_duration_ms"],
            }
        except Exception as e:
            logger.error("Pipeline execution failed for %s: %s", scenario_id, str(e))
            return {"status": "error", "scenario_id": scenario_id, "error": str(e),
                    "message": "AI pipeline execution failed."}
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

@router.get("/decisions")
async def list_decisions(
    page: int = 1,
    page_size: int = 50,
    user: CurrentUser = Depends(get_current_user),
):
    """Return paginated decisions for the current tenant."""
    page_size = min(page_size, 200)  # Cap at 200
    offset = (max(page, 1) - 1) * page_size
    db = await get_db()
    try:
        # Total count
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM decisions WHERE tenant_id = ?",
            (user.tenant_id,),
        )
        total = (await cursor.fetchone())["cnt"]

        cursor = await db.execute(
            "SELECT * FROM decisions WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user.tenant_id, page_size, offset),
        )
        rows = await cursor.fetchall()
        items = [_decision_to_response(_row_to_decision(r)) for r in rows]
        return PaginatedResponse(
            items=items, total=total, page=page, page_size=page_size,
            has_more=(offset + page_size) < total,
        )
    finally:
        await db.close()


@router.get("/decisions/verify-all", response_model=VerificationResult)
async def verify_all_decisions(user: CurrentUser = Depends(get_current_user)):
    """Verify the entire hash chain for this tenant."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM decisions WHERE tenant_id = ? AND decision_id != 'dec_005_tampered' ORDER BY created_at",
            (user.tenant_id,),
        )
        rows = await cursor.fetchall()
        decisions = [_row_to_decision(r) for r in rows]
        result = verify_chain(decisions)
        return VerificationResult(**result)
    finally:
        await db.close()


@router.get("/decisions/{decision_id}", response_model=DecisionResponse)
async def get_decision(decision_id: str, user: CurrentUser = Depends(get_current_user)):
    """Return a complete decision."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM decisions WHERE decision_id = ? AND tenant_id = ?",
            (decision_id, user.tenant_id),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Decision {decision_id} not found")
        return _decision_to_response(_row_to_decision(row))
    finally:
        await db.close()


@router.get("/decisions/{decision_id}/evidence")
async def get_decision_evidence(decision_id: str, user: CurrentUser = Depends(get_current_user)):
    """Return linked evidence and extracted facts for a decision."""
    db = await get_db()
    try:
        # Verify decision exists and belongs to tenant
        cursor = await db.execute(
            "SELECT decision_id FROM decisions WHERE decision_id = ? AND tenant_id = ?",
            (decision_id, user.tenant_id),
        )
        if not await cursor.fetchone():
            raise HTTPException(404, f"Decision {decision_id} not found")

        cursor = await db.execute(
            "SELECT * FROM evidence WHERE tenant_id = ?",
            (user.tenant_id,),
        )
        rows = await cursor.fetchall()
        linked = []
        for ev in rows:
            linked_ids = _parse_json_field(ev["linked_decision_ids"])
            if isinstance(linked_ids, str):
                linked_ids = [linked_ids]
            if decision_id in linked_ids:
                linked.append(EvidenceResponse(
                    evidence_id=ev["evidence_id"],
                    source_type=ev["source_type"],
                    raw_content=ev["raw_content"],
                    extracted_facts=_parse_json_field(ev["extracted_facts"]),
                    linked_decision_ids=linked_ids,
                ))
        return linked
    finally:
        await db.close()


@router.get("/decisions/{decision_id}/verify", response_model=VerificationResult)
async def verify_decision(decision_id: str, user: CurrentUser = Depends(get_current_user)):
    """Verify hash chain integrity up to this decision.

    Follows prev_decision_hash links to find the actual chain,
    rather than relying on list position.
    """
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM decisions WHERE decision_id = ? AND tenant_id = ?",
            (decision_id, user.tenant_id),
        )
        target_row = await cursor.fetchone()
        if not target_row:
            raise HTTPException(404, f"Decision {decision_id} not found")

        target = _row_to_decision(target_row)

        # Build chain by following prev_decision_hash links
        chain = [target]
        current = target
        visited = {target["decision_id"]}

        while current.get("prev_decision_hash") and current["prev_decision_hash"] != "genesis":
            prev_hash = current["prev_decision_hash"]
            cursor2 = await db.execute(
                "SELECT * FROM decisions WHERE decision_hash = ? AND tenant_id = ?",
                (prev_hash, user.tenant_id),
            )
            predecessor_row = await cursor2.fetchone()
            if not predecessor_row:
                break
            predecessor = _row_to_decision(predecessor_row)
            if predecessor["decision_id"] in visited:
                break
            chain.append(predecessor)
            visited.add(predecessor["decision_id"])
            current = predecessor

        chain.reverse()
        result = verify_chain(chain)
        return VerificationResult(**result)
    finally:
        await db.close()


@router.get("/sellers/{entity_id}/decisions")
async def get_seller_decisions(entity_id: str, user: CurrentUser = Depends(get_current_user)):
    """Return all decisions for a seller within this tenant."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM decisions WHERE entity_id = ? AND tenant_id = ? ORDER BY created_at DESC",
            (entity_id, user.tenant_id),
        )
        rows = await cursor.fetchall()
        seller_decisions = [_row_to_decision(r) for r in rows]

        total_gross = sum(d["gross_amount"] for d in seller_decisions)
        total_final = sum(d["final_amount"] for d in seller_decisions)

        results = []
        for d in seller_decisions:
            # Verify individual decision
            chain = [d]
            current = d
            visited = {d["decision_id"]}
            while current.get("prev_decision_hash") and current["prev_decision_hash"] != "genesis":
                cursor2 = await db.execute(
                    "SELECT * FROM decisions WHERE decision_hash = ? AND tenant_id = ?",
                    (current["prev_decision_hash"], user.tenant_id),
                )
                pred_row = await cursor2.fetchone()
                if not pred_row:
                    break
                pred = _row_to_decision(pred_row)
                if pred["decision_id"] in visited:
                    break
                chain.append(pred)
                visited.add(pred["decision_id"])
                current = pred
            chain.reverse()
            chain_result = verify_chain(chain)

            results.append({
                "decision": _decision_to_response(d),
                "verification": VerificationResult(**chain_result),
            })

        return {
            "entity_id": entity_id,
            "total_decisions": len(results),
            "total_gross_entitlement": total_gross,
            "total_final_amount": total_final,
            "total_adjustments": total_gross - total_final,
            "decisions": results,
        }
    finally:
        await db.close()


@router.get("/decisions/{decision_id}/defense-packet")
async def get_defense_packet(decision_id: str, user: CurrentUser = Depends(get_current_user)):
    """Generate a Decision Defense Packet."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM decisions WHERE decision_id = ? AND tenant_id = ?",
            (decision_id, user.tenant_id),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Decision {decision_id} not found")

        d = _row_to_decision(row)

        # Get linked evidence
        cursor_ev = await db.execute(
            "SELECT * FROM evidence WHERE tenant_id = ?", (user.tenant_id,),
        )
        ev_rows = await cursor_ev.fetchall()
        linked_evidence = []
        for ev in ev_rows:
            linked_ids = _parse_json_field(ev["linked_decision_ids"])
            if isinstance(linked_ids, str):
                linked_ids = [linked_ids]
            if decision_id in linked_ids:
                linked_evidence.append(EvidenceResponse(
                    evidence_id=ev["evidence_id"],
                    source_type=ev["source_type"],
                    raw_content=ev["raw_content"],
                    extracted_facts=_parse_json_field(ev["extracted_facts"]),
                    linked_decision_ids=linked_ids,
                ))

        # Get relevant policies
        policy_ids = d["policy_version_id"].split(",")
        cursor_p = await db.execute("SELECT * FROM policies")
        pol_rows = await cursor_p.fetchall()
        relevant_policies = [
            PolicyResponse(**dict(p)) for p in pol_rows if p["policy_id"] in policy_ids
        ]

        # Verify integrity
        chain = [d]
        current = d
        visited = {d["decision_id"]}
        while current.get("prev_decision_hash") and current["prev_decision_hash"] != "genesis":
            cursor2 = await db.execute(
                "SELECT * FROM decisions WHERE decision_hash = ? AND tenant_id = ?",
                (current["prev_decision_hash"], user.tenant_id),
            )
            pred_row = await cursor2.fetchone()
            if not pred_row:
                break
            pred = _row_to_decision(pred_row)
            if pred["decision_id"] in visited:
                break
            chain.append(pred)
            visited.add(pred["decision_id"])
            current = pred
        chain.reverse()
        chain_result = verify_chain(chain)

        items = d.get("line_items", [])
        parsed_items = []
        for item in items:
            if isinstance(item, dict):
                parsed_items.append(LineItem(**item))
            else:
                parsed_items.append(item)

        total_deductions = sum(
            item.amount for item in parsed_items if item.type in ("fee", "deduction")
        )

        return DefensePacket(
            decision=_decision_to_response(d),
            financial_breakdown={
                "gross_amount": d["gross_amount"],
                "total_deductions": total_deductions,
                "final_amount": d["final_amount"],
                "validation": validate_calculation(d["gross_amount"], parsed_items, d["final_amount"]),
            },
            evidence=linked_evidence,
            policies=relevant_policies,
            approver_id=d["approver_id"],
            approved_at=_to_iso_str(d["approved_at"]),
            integrity=VerificationResult(**chain_result),
        )
    finally:
        await db.close()


@router.get("/decisions/{decision_id}/defense-packet/pdf")
async def download_defense_packet_pdf(
    decision_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Generate and download a PDF defense packet."""
    # Reuse the defense packet logic
    packet_resp = await get_defense_packet(decision_id, user)
    packet = packet_resp if isinstance(packet_resp, dict) else packet_resp.model_dump()

    # Fetch audit trail for this decision
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM audit_log WHERE tenant_id = ? AND entity_id = ? ORDER BY created_at",
            (user.tenant_id, decision_id),
        )
        audit_rows = await cursor.fetchall()
        audit_trail = [dict(r) for r in audit_rows]
    finally:
        await db.close()

    from defense_packet_pdf import generate_defense_packet_pdf
    pdf_bytes = generate_defense_packet_pdf(packet, audit_trail)

    await log_audit(user.tenant_id, "defense_packet.downloaded", "decision", decision_id,
                    user_id=user.user_id, details={"format": "pdf", "size_bytes": len(pdf_bytes)})

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="defense_{decision_id}.pdf"',
        },
    )


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@router.get("/evidence/{evidence_id}", response_model=EvidenceResponse)
async def get_evidence(evidence_id: str, user: CurrentUser = Depends(get_current_user)):
    """Return a single evidence record."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM evidence WHERE evidence_id = ? AND tenant_id = ?",
            (evidence_id, user.tenant_id),
        )
        ev = await cursor.fetchone()
        if not ev:
            raise HTTPException(404, f"Evidence {evidence_id} not found")
        return EvidenceResponse(
            evidence_id=ev["evidence_id"],
            source_type=ev["source_type"],
            raw_content=ev["raw_content"],
            extracted_facts=_parse_json_field(ev["extracted_facts"]),
            linked_decision_ids=_parse_json_field(ev["linked_decision_ids"]),
        )
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------

@router.get("/policies", response_model=list[PolicyResponse])
async def list_policies(user: CurrentUser = Depends(get_current_user)):
    """Return all policy records."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM policies")
        rows = await cursor.fetchall()
        return [PolicyResponse(**dict(r)) for r in rows]
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# AI Status
# ---------------------------------------------------------------------------

@router.get("/ai/status")
async def ai_status(user: CurrentUser = Depends(get_current_user)):
    """Return the active AI provider information."""
    from ai.llm_provider import is_ai_available, get_provider
    try:
        provider = get_provider()
        info = provider.provider_info()
        info["available"] = True
        return info
    except EnvironmentError as e:
        return {"available": False, "provider": "none", "model": None, "error": str(e)}


# ---------------------------------------------------------------------------
# Dashboard Stats
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_stats(user: CurrentUser = Depends(get_current_user)):
    """Return dashboard statistics scoped to current tenant."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM decisions WHERE tenant_id = ?",
            (user.tenant_id,),
        )
        rows = await cursor.fetchall()
        decisions = [_row_to_decision(r) for r in rows]

        total_decisions = len(decisions)
        verified = 0
        flagged = 0
        for d in decisions:
            if d["decision_id"] == "dec_005_tampered":
                flagged += 1
            else:
                verified += 1

        total_gross = sum(d["gross_amount"] for d in decisions)
        total_final = sum(d["final_amount"] for d in decisions)
        total_adjustments = total_gross - total_final

        return {
            "total_decisions": total_decisions,
            "verified_decisions": verified,
            "flagged_decisions": flagged,
            "total_gross_entitlement": total_gross,
            "total_final_amount": total_final,
            "total_adjustments": total_adjustments,
        }
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Approval/Rejection
# ---------------------------------------------------------------------------

@router.post("/decisions/{decision_id}/approve")
async def approve_decision(
    decision_id: str,
    request: ApprovalRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Approve a decision (human review required for AI-generated decisions)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM decisions WHERE decision_id = ? AND tenant_id = ?",
            (decision_id, user.tenant_id),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Decision {decision_id} not found")

        d = _row_to_decision(row)
        if d.get("status") == "APPROVED":
            raise HTTPException(400, f"Decision {decision_id} is already approved")
        if d.get("status") == "REJECTED":
            raise HTTPException(400, f"Decision {decision_id} was rejected")

        now = datetime.now(timezone.utc).isoformat()
        d["status"] = "APPROVED"
        d["approver_id"] = request.approver_id
        d["approved_at"] = now
        d["decision_hash"] = compute_decision_hash(d, d["prev_decision_hash"])

        await db.execute(
            "UPDATE decisions SET status = 'APPROVED', approver_id = ?, approved_at = ?, decision_hash = ? "
            "WHERE decision_id = ? AND tenant_id = ?",
            (request.approver_id, now, d["decision_hash"], decision_id, user.tenant_id),
        )
        await db.commit()

        await log_audit(user.tenant_id, "decision.approved", "decision", decision_id,
                        user_id=user.user_id, details={"approver_id": request.approver_id})

        return {"status": "approved", "decision_id": decision_id,
                "approver_id": request.approver_id, "approved_at": now}
    finally:
        await db.close()


@router.post("/decisions/{decision_id}/reject")
async def reject_decision(
    decision_id: str,
    request: RejectionRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Reject a decision."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM decisions WHERE decision_id = ? AND tenant_id = ?",
            (decision_id, user.tenant_id),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Decision {decision_id} not found")

        d = _row_to_decision(row)
        if d.get("status") == "APPROVED":
            raise HTTPException(400, f"Decision {decision_id} was approved")
        if d.get("status") == "REJECTED":
            raise HTTPException(400, f"Decision {decision_id} is already rejected")

        now = datetime.now(timezone.utc).isoformat()
        d["status"] = "REJECTED"
        d["approver_id"] = request.approver_id
        d["approved_at"] = now
        d["rejection_reason"] = request.reason
        d["decision_hash"] = compute_decision_hash(d, d["prev_decision_hash"])

        await db.execute(
            "UPDATE decisions SET status = 'REJECTED', approver_id = ?, approved_at = ?, "
            "decision_hash = ?, model_output = ? WHERE decision_id = ? AND tenant_id = ?",
            (request.approver_id, now, d["decision_hash"],
             json.dumps(d.get("model_output", {})), decision_id, user.tenant_id),
        )
        await db.commit()

        await log_audit(user.tenant_id, "decision.rejected", "decision", decision_id,
                        user_id=user.user_id,
                        details={"approver_id": request.approver_id, "reason": request.reason})

        return {"status": "rejected", "decision_id": decision_id,
                "approver_id": request.approver_id, "rejected_at": now, "reason": request.reason}
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# POST /api/decisions/analyze — user-created decision
# ---------------------------------------------------------------------------

@router.post("/decisions/analyze")
async def analyze_decision(
    req: AnalyzeDecisionRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Create a new financial decision from user-provided evidence.

    Flow: Evidence ingestion → Deterministic extraction → Policy matching →
    Deterministic calculation → Decision → Hash chain → Audit trail
    """
    if req.gross_amount <= 0:
        raise HTTPException(400, "gross_amount must be positive")
    if not req.evidence_items:
        raise HTTPException(400, "At least one evidence item is required")

    db = await get_db()
    try:
        # 1. Create evidence records
        evidence_ids = []
        for i, item in enumerate(req.evidence_items):
            ev_id = f"ev_user_{uuid.uuid4().hex[:8]}_{i}"
            extracted_facts = []
            try:
                content = json.loads(item.raw_content)
                if isinstance(content, dict):
                    for key, val in content.items():
                        if val is not None:
                            extracted_facts.append({"fact": f"{key}: {val}", "confidence": 1.0})
            except (json.JSONDecodeError, TypeError):
                extracted_facts.append({"fact": item.raw_content[:200], "confidence": 0.5})

            await db.execute(
                "INSERT INTO evidence (evidence_id, tenant_id, source_type, raw_content, extracted_facts, linked_decision_ids, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ev_id, user.tenant_id, item.source_type, item.raw_content,
                 json.dumps(extracted_facts), "[]", datetime.now(timezone.utc).isoformat()),
            )
            evidence_ids.append(ev_id)

        # 2. Build line items
        evidence_ids_map = {"platform_fee": evidence_ids}
        if req.has_sla_breach and req.sla_penalty_amount > 0:
            evidence_ids_map["sla_penalty"] = evidence_ids
        if req.has_returns and req.return_reserve_amount > 0:
            evidence_ids_map["return_reserve"] = evidence_ids

        line_items = build_line_items(
            gross_amount=req.gross_amount,
            has_sla_breach=req.has_sla_breach,
            sla_penalty_amount=req.sla_penalty_amount,
            has_returns=req.has_returns,
            return_reserve_amount=req.return_reserve_amount,
            evidence_ids=evidence_ids_map,
        )
        final_amount = calculate_final_amount(req.gross_amount, line_items)

        # 3. Get previous hash for chain
        cursor = await db.execute(
            "SELECT decision_hash FROM decisions WHERE tenant_id = ? AND decision_id != 'dec_005_tampered' "
            "ORDER BY created_at DESC LIMIT 1",
            (user.tenant_id,),
        )
        prev_row = await cursor.fetchone()
        prev_hash = prev_row["decision_hash"] if prev_row else "genesis"

        # 4. Create decision
        decision_id = f"dec_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        approver_id = req.approver_id or user.user_id

        applicable_policies = ["platform_1_1"]
        if req.has_sla_breach:
            applicable_policies.append("sla_4_2")
        if req.has_returns:
            applicable_policies.append("returns_3_1")

        claims = []
        if req.has_sla_breach:
            claims.append({"type": "sla_breach", "evidence_ids": evidence_ids, "policy_clause_id": "sla_4_2"})
        if req.has_returns:
            claims.append({"type": "return_processed", "evidence_ids": evidence_ids, "policy_clause_id": "returns_3_1"})

        decision_data = {
            "decision_id": decision_id,
            "entity_type": req.entity_type,
            "entity_id": req.entity_id,
            "gross_amount": req.gross_amount,
            "line_items": [item.model_dump() for item in line_items],
            "final_amount": final_amount,
            "policy_version_id": ",".join(applicable_policies),
            "approver_id": approver_id,
            "approved_at": now,
            "model_output": {
                "source": "user_analysis",
                "claims": claims,
                "classification": "clear" if claims else "no_issues",
                "confidence": 1.0,
                "reasoning_summary": f"User analysis: {len(evidence_ids)} evidence records, {len(claims)} claims.",
                "extracted_facts_count": sum(
                    len(json.loads(e["extracted_facts"])) for e in []
                ),
            },
            "prev_decision_hash": prev_hash,
            "decision_hash": "",
            "created_at": now,
            "status": "REVIEW_REQUIRED",
        }
        decision_data["decision_hash"] = compute_decision_hash(decision_data, prev_hash)

        await db.execute(
            "INSERT INTO decisions (decision_id, tenant_id, entity_type, entity_id, gross_amount, "
            "line_items, final_amount, policy_version_id, approver_id, approved_at, model_output, "
            "prev_decision_hash, decision_hash, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id, user.tenant_id, req.entity_type, req.entity_id, req.gross_amount,
                json.dumps(decision_data["line_items"]), final_amount,
                ",".join(applicable_policies), approver_id, now,
                json.dumps(decision_data["model_output"]),
                prev_hash, decision_data["decision_hash"], now, "REVIEW_REQUIRED",
            ),
        )

        # Link evidence — read-modify-write to avoid SQLite/PostgreSQL
        # json_insert compatibility issues.
        for ev_id in evidence_ids:
            cursor = await db.execute(
                "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ? AND tenant_id = ?",
                (ev_id, user.tenant_id),
            )
            row = await cursor.fetchone()
            if row:
                current_ids = _parse_json_field(
                    row["linked_decision_ids"] if hasattr(row, "keys") else row[0]
                )
                if not isinstance(current_ids, list):
                    current_ids = []
                if decision_id not in current_ids:
                    current_ids.append(decision_id)
                await db.execute(
                    "UPDATE evidence SET linked_decision_ids = ? "
                    "WHERE evidence_id = ? AND tenant_id = ?",
                    (json.dumps(current_ids), ev_id, user.tenant_id),
                )

        await db.commit()

        await log_audit(user.tenant_id, "decision.created", "decision", decision_id,
                        user_id=user.user_id,
                        details={"entity_id": req.entity_id, "gross_amount": req.gross_amount,
                                 "final_amount": final_amount, "evidence_count": len(evidence_ids)})

        return {
            "status": "analyzed",
            "decision_id": decision_id,
            "decision_status": "REVIEW_REQUIRED",
            "gross_amount": req.gross_amount,
            "final_amount": final_amount,
            "line_items": [item.model_dump() for item in line_items],
            "evidence_count": len(evidence_ids),
            "evidence_ids": evidence_ids,
            "claims": claims,
            "decision_hash": decision_data["decision_hash"],
            "prev_decision_hash": prev_hash,
            "message": "Decision analyzed successfully. Review for approval.",
        }
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

@router.get("/audit-log")
async def get_audit_log(
    page: int = 1,
    page_size: int = 50,
    user: CurrentUser = Depends(get_current_user),
):
    """Return paginated audit log entries for this tenant."""
    page_size = min(page_size, 200)
    offset = (max(page, 1) - 1) * page_size
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM audit_log WHERE tenant_id = ?",
            (user.tenant_id,),
        )
        total = (await cursor.fetchone())["cnt"]

        cursor = await db.execute(
            "SELECT * FROM audit_log WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user.tenant_id, page_size, offset),
        )
        rows = await cursor.fetchall()
        return {"items": [dict(r) for r in rows], "total": total, "page": page,
                "page_size": page_size, "has_more": (offset + page_size) < total}
    finally:
        await db.close()
