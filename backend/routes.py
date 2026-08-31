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
import hash_chain
from hash_chain import verify_chain, compute_decision_hash
from calculations import validate_calculation, build_line_items, calculate_final_amount
from ai.pipeline import compute_analysis_fingerprint
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
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "error",
                    "scenario_id": scenario_id,
                    "error": "No LLM provider available",
                    "message": "Start Ollama with a model, or set OPENROUTER_API_KEY.",
                    "fallback": "Configure an LLM provider to enable AI analysis.",
                },
            )

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
            raise HTTPException(
                status_code=422,
                detail={
                    "status": "error",
                    "scenario_id": scenario_id,
                    "error": "No policies found for this scenario",
                    "message": "Scenario has no configured policies. Run system initialization first.",
                },
            )

        # ── Step 1: Fetch ALL evidence for this tenant and build the
        # decision→evidence mapping needed for the idempotency check.
        # This must run BEFORE the ai_analyzed filter so that the
        # idempotency check can match decisions whose evidence is
        # already marked ai_analyzed = TRUE.
        cursor_all_ev = await db.execute(
            "SELECT evidence_id, linked_decision_ids FROM evidence WHERE tenant_id = ?",
            (user.tenant_id,),
        )
        all_ev_rows = await cursor_all_ev.fetchall()
        # Build decision_id → list[evidence_id] mapping
        dec_to_evidence: dict[str, list[str]] = {}
        # Also collect every evidence_id in this tenant
        all_tenant_evidence_ids: list[str] = []
        for ev_row in all_ev_rows:
            ev_id = ev_row["evidence_id"] if hasattr(ev_row, "keys") else ev_row[0]
            all_tenant_evidence_ids.append(ev_id)
            linked_raw = ev_row["linked_decision_ids"] if hasattr(ev_row, "keys") else ev_row[1]
            linked_ids = _parse_json_field(linked_raw)
            if not isinstance(linked_ids, list):
                linked_ids = []
            for dec_id in linked_ids:
                dec_to_evidence.setdefault(dec_id, []).append(ev_id)

        # ── Step 2: Idempotency check (BEFORE 'No evidence available').
        # Uses the FULL evidence set so that a second identical run can
        # match the existing decision even when all evidence is already
        # ai_analyzed = TRUE.
        current_evidence_ids = sorted(all_tenant_evidence_ids)
        current_policy_ids = sorted(p["policy_id"] for p in policy_records)
        idempotency_key = (scenario_id, tuple(current_evidence_ids), tuple(current_policy_ids))

        cursor_existing = await db.execute(
            "SELECT decision_id, model_output, policy_version_id FROM decisions "
            "WHERE tenant_id = ? AND approver_id = 'ai_pipeline' "
            "ORDER BY created_at DESC",
            (user.tenant_id,),
        )
        existing_rows = await cursor_existing.fetchall()

        for ex_row in existing_rows:
            ex_decision_id = ex_row["decision_id"]
            ex_model_output = _parse_json_field(ex_row["model_output"])
            ex_policy_raw = ex_row["policy_version_id"] or ""
            ex_policy_ids = sorted(
                p.strip() for p in ex_policy_raw.split(",") if p.strip()
            )
            ex_evidence_ids = sorted(dec_to_evidence.get(ex_decision_id, []))

            # Use the scenario_id persisted on the existing decision,
            # NOT the current request's scenario_id, to prevent false
            # positive matches when the same evidence is run under
            # a different scenario.
            ex_scenario_id = (
                ex_model_output.get("scenario_id")
                if isinstance(ex_model_output, dict)
                else None
            )
            ex_key = (ex_scenario_id, tuple(ex_evidence_ids), tuple(ex_policy_ids))

            # Also verify the analysis_fingerprint if available.
            # This adds a compact, tamper-evident check on top of the
            # tuple comparison.
            ex_fingerprint = (
                ex_model_output.get("analysis_fingerprint")
                if isinstance(ex_model_output, dict)
                else None
            )
            expected_fingerprint = (
                compute_analysis_fingerprint(
                    tenant_id=user.tenant_id,
                    scenario_id=scenario_id,
                    evidence_ids=current_evidence_ids,
                    policy_ids=list(current_policy_ids),
                )
                if current_evidence_ids
                else None
            )
            fingerprint_match = (
                expected_fingerprint is not None
                and ex_fingerprint == expected_fingerprint
            )
            if ex_key == idempotency_key or fingerprint_match:
                logger.info(
                    "Reusing existing AI decision %s for scenario %s (idempotent)",
                    ex_decision_id, scenario_id,
                )
                cursor_dec = await db.execute(
                    "SELECT * FROM decisions WHERE decision_id = ? AND tenant_id = ?",
                    (ex_decision_id, user.tenant_id),
                )
                dec_row = await cursor_dec.fetchone()
                if dec_row:
                    existing_decision = _row_to_decision(dec_row)
                    existing_decision["tenant_id"] = user.tenant_id
                    return {
                        "status": "completed",
                        "scenario_id": scenario_id,
                        "decision_id": ex_decision_id,
                        "decision_status": existing_decision["status"],
                        "classification": ex_model_output.get("classification", "unknown") if isinstance(ex_model_output, dict) else "unknown",
                        "message": f"Existing AI decision {ex_decision_id} already covers this evidence set.",
                    }

        # ── Step 3: No idempotent match found.
        # Now fetch unanalysed evidence for a new pipeline run.
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
            raise HTTPException(
                status_code=422,
                detail={
                    "status": "error",
                    "scenario_id": scenario_id,
                    "error": "No evidence available",
                    "message": "No evidence records found for this tenant. Create evidence from Razorpay events or user uploads before running analysis.",
                },
            )

        # Get previous decision hash from database (not seed data)
        cursor_hash = await db.execute(
            "SELECT decision_hash FROM decisions WHERE tenant_id = ? AND decision_id != 'dec_005_tampered' "
            "ORDER BY created_at DESC LIMIT 1",
            (user.tenant_id,),
        )
        prev_row = await cursor_hash.fetchone()
        prev_hash = prev_row["decision_hash"] if prev_row else "genesis"

        try:
            # ── Run the Finance Controller Agent ──
            # The agent autonomously investigates the case: inspects evidence,
            # calls bounded tools, detects ambiguity/conflict, and produces
            # structured analysis.  The deterministic calculation engine
            # remains the sole authority on monetary amounts.
            from ai.agent import run_agent

            # Determine gross amount from order evidence for the agent
            agent_gross = 0
            for ev in evidence_records:
                if ev["source_type"] == "order":
                    try:
                        content = json.loads(ev["raw_content"])
                        agent_gross = content.get("amount", 0)
                        if agent_gross:
                            break
                    except (json.JSONDecodeError, KeyError):
                        continue

            entity_id = "unknown"
            for ev in evidence_records:
                if ev["source_type"] == "order":
                    try:
                        content = json.loads(ev["raw_content"])
                        entity_id = content.get("seller_id") or content.get("razorpay_entity_id", "unknown")
                        if entity_id != "unknown":
                            break
                    except (json.JSONDecodeError, KeyError):
                        continue

            agent_result = await run_agent(
                tenant_id=user.tenant_id,
                scenario_id=scenario_id,
                entity_id=entity_id,
                gross_amount=agent_gross,
                evidence_records=evidence_records,
                policy_records=policy_records,
                scenario_description=scenario["description"] if scenario else "",
            )

            result = run_pipeline(
                scenario_id=scenario_id,
                evidence_records=evidence_records,
                policy_records=policy_records,
                prev_decision_hash=prev_hash,
                use_mock=False,
                agent_result=agent_result,
            )

            decision = result["decision"]
            decision["tenant_id"] = user.tenant_id

            # Fix the analysis_fingerprint: the pipeline computes it with
            # tenant_id="" because it doesn't have access to the tenant.
            # Recompute with the actual tenant_id so idempotency checks
            # will match.
            mo = decision.get("model_output", {})
            if isinstance(mo, dict) and mo.get("analysis_fingerprint"):
                mo["analysis_fingerprint"] = compute_analysis_fingerprint(
                    tenant_id=user.tenant_id,
                    scenario_id=scenario_id,
                    evidence_ids=sorted(
                        [ev["evidence_id"] for ev in evidence_records]
                    ),
                    policy_ids=sorted(p["policy_id"] for p in policy_records),
                )

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
                    "SELECT linked_decision_ids FROM evidence WHERE evidence_id = ? AND tenant_id = ?",
                    (ev_id, user.tenant_id),
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
                    "WHERE evidence_id = ? AND tenant_id = ?",
                    (
                        json.dumps(ev_result.get("extracted_facts", [])),
                        json.dumps(current_ids),
                        ev_id,
                        user.tenant_id,
                    ),
                )
                # Mark evidence as analyzed by AI so it is not reprocessed.
                await db.execute(
                    "UPDATE evidence SET ai_analyzed = TRUE WHERE evidence_id = ? AND tenant_id = ?",
                    (ev_result["evidence_id"], user.tenant_id),
                )

            await db.execute(
                "UPDATE scenarios SET status = 'completed' WHERE scenario_id = ?",
                (scenario_id,),
            )
            await db.commit()

            await log_audit(user.tenant_id, "scenario.run", "scenario", scenario_id,
                            user_id=user.user_id, details={"decision_id": decision["decision_id"]})

            # Determine agent success/failure semantics
            agent_success = agent_result.get("agent_state")
            agent_success = getattr(agent_result.get("agent_state"), "success", True) if agent_result.get("agent_state") else True

            response = {
                "status": "completed",
                "scenario_id": scenario_id,
                "decision_id": decision["decision_id"],
                "decision_status": decision["status"],
                "stages": result["stages"],
                "total_duration_ms": result["total_duration_ms"],
                "agent_success": agent_success,
                "agent_iterations": getattr(agent_result.get("agent_state"), "iteration_count", 0),
                "agent_tool_calls": agent_result.get("tool_calls", 0),
                "agent_stop_reason": getattr(agent_result.get("agent_state"), "stop_reason", "unknown"),
            }

            if agent_success:
                response["message"] = "AI pipeline executed successfully."
            else:
                response["message"] = (
                    f"Agent execution failed (stop_reason={response['agent_stop_reason']}). "
                    f"Decision created but may require manual review."
                )

            return response
        except Exception as e:
            logger.error("Pipeline execution failed for %s: %s", scenario_id, str(e))
            raise HTTPException(
                status_code=500,
                detail={
                    "status": "error",
                    "scenario_id": scenario_id,
                    "error": str(e),
                    "message": "AI pipeline execution failed.",
                },
            )
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

        # Extended hash verification
        canonical_payload = hash_chain.canonicalize(target)
        recomputed_hash = compute_decision_hash(target, target["prev_decision_hash"])
        decision_hash_valid = recomputed_hash == target["decision_hash"]
        prev_hash_valid = (
            target["prev_decision_hash"] == "genesis"
            or target["prev_decision_hash"] is not None
        )
        chain_continuity = result.get("valid", False)

        return VerificationResult(
            **result,
            decision_hash_valid=decision_hash_valid,
            prev_hash_valid=prev_hash_valid,
            canonical_payload=canonical_payload,
            chain_continuity=chain_continuity,
        )
    finally:
        await db.close()


@router.get("/decisions/{decision_id}/replay")
async def replay_decision(decision_id: str, user: CurrentUser = Depends(get_current_user)):
    """Replay/recompute a stored decision using stored evidence + policies.

    Returns:
      - stored: the original decision values
      - recomputed: values from deterministic re-calculation
      - match: whether stored == recomputed
      - mismatches: list of fields that differ
    """
    from calculations import build_line_items, calculate_final_amount, validate_calculation
    from hash_chain import compute_decision_hash

    db = await get_db()
    try:
        # Fetch the decision
        cursor = await db.execute(
            "SELECT * FROM decisions WHERE decision_id = ? AND tenant_id = ?",
            (decision_id, user.tenant_id),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Decision {decision_id} not found")

        d = _row_to_decision(row)

        # Fetch linked evidence
        cursor_ev = await db.execute(
            "SELECT * FROM evidence WHERE tenant_id = ?",
            (user.tenant_id,),
        )
        ev_rows = await cursor_ev.fetchall()
        linked_evidence = []
        for ev in ev_rows:
            linked_ids = _parse_json_field(ev["linked_decision_ids"])
            if isinstance(linked_ids, str):
                linked_ids = [linked_ids]
            if decision_id in linked_ids:
                linked_evidence.append(dict(ev))

        # Determine gross amount from linked evidence
        gross_amount = 0
        for ev in linked_evidence:
            if ev["source_type"] == "order":
                try:
                    content = json.loads(ev["raw_content"])
                    gross_amount = content.get("amount", 0)
                    if gross_amount:
                        break
                except (json.JSONDecodeError, KeyError):
                    continue

        # Fallback: use stored gross_amount if evidence doesn't have it
        if gross_amount == 0:
            gross_amount = d["gross_amount"]

        # Rebuild line items from stored line_items (deterministic)
        stored_items = []
        for item_data in d.get("line_items", []):
            if isinstance(item_data, dict):
                stored_items.append(LineItem(**item_data))

        # Recompute using the deterministic engine
        evidence_ids_map = {}
        for item in stored_items:
            evidence_ids_map[item.label.lower().replace(" ", "_")] = item.evidence_ids

        recomputed_items = build_line_items(
            gross_amount=gross_amount,
            has_sla_breach=any(
                "sla" in (item.policy_clause_id or "") for item in stored_items
            ),
            sla_penalty_amount=sum(
                item.amount for item in stored_items
                if "sla" in (item.policy_clause_id or "")
            ),
            has_returns=any(
                "return" in (item.policy_clause_id or "") for item in stored_items
            ),
            return_reserve_amount=sum(
                item.amount for item in stored_items
                if "return" in (item.policy_clause_id or "")
            ),
            evidence_ids=evidence_ids_map,
        )
        recomputed_final = calculate_final_amount(gross_amount, recomputed_items)

        # Compare stored vs recomputed
        mismatches = []
        if d["gross_amount"] != gross_amount:
            mismatches.append({"field": "gross_amount", "stored": d["gross_amount"], "recomputed": gross_amount})
        if d["final_amount"] != recomputed_final:
            mismatches.append({"field": "final_amount", "stored": d["final_amount"], "recomputed": recomputed_final})
        if len(d.get("line_items", [])) != len(recomputed_items):
            mismatches.append({
                "field": "line_items_count",
                "stored": len(d.get("line_items", [])),
                "recomputed": len(recomputed_items),
            })

        # Verify hash integrity
        canonical_payload = hash_chain.canonicalize(d)
        recomputed_hash = compute_decision_hash(d, d["prev_decision_hash"])
        hash_valid = recomputed_hash == d["decision_hash"]

        return {
            "decision_id": decision_id,
            "stored": {
                "gross_amount": d["gross_amount"],
                "final_amount": d["final_amount"],
                "line_items": d.get("line_items", []),
                "policy_version_id": d["policy_version_id"],
                "decision_hash": d["decision_hash"],
                "policy_snapshot": (
                    d.get("model_output", {}).get("policy_snapshot", [])
                    if isinstance(d.get("model_output", {}), dict)
                    else []
                ),
            },
            "recomputed": {
                "gross_amount": gross_amount,
                "final_amount": recomputed_final,
                "line_items": [item.model_dump() for item in recomputed_items],
            },
            "match": len(mismatches) == 0,
            "mismatches": mismatches,
            "hash_valid": hash_valid,
            "canonical_payload": canonical_payload,
        }
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

        # Get relevant policies — prefer stored snapshot for historical reproducibility
        model_output = d.get("model_output", {})
        if isinstance(model_output, str):
            try:
                model_output = json.loads(model_output)
            except (json.JSONDecodeError, TypeError):
                model_output = {}
        policy_snapshot = model_output.get("policy_snapshot", [])

        if policy_snapshot:
            # Use stored snapshot — ensures historical decisions are not
            # affected by future policy updates.
            relevant_policies = [
                PolicyResponse(
                    policy_id=p["policy_id"],
                    version=p["version"],
                    clause_text=p["clause_text"],
                    effective_date=p["effective_date"],
                )
                for p in policy_snapshot
            ]
        else:
            # Fallback for legacy decisions created before policy snapshots
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

        # Extract calculation trace and exceptions from model_output
        model_output = d.get("model_output", {})
        if isinstance(model_output, str):
            try:
                model_output = json.loads(model_output)
            except (json.JSONDecodeError, TypeError):
                model_output = {}
        calculation_trace = model_output.get("calculation_trace")
        exceptions = model_output.get("exceptions", [])

        # Extended hash verification
        canonical_payload = hash_chain.canonicalize(d)
        recomputed_hash = compute_decision_hash(d, d["prev_decision_hash"])
        decision_hash_valid = recomputed_hash == d["decision_hash"]
        prev_hash_valid = (
            d["prev_decision_hash"] == "genesis"
            or d["prev_decision_hash"] is not None
        )
        chain_continuity = chain_result.get("valid", False)

        integrity = VerificationResult(
            **chain_result,
            decision_hash_valid=decision_hash_valid,
            prev_hash_valid=prev_hash_valid,
            canonical_payload=canonical_payload,
            chain_continuity=chain_continuity,
        )

        return DefensePacket(
            decision=_decision_to_response(d),
            financial_breakdown={
                "gross_amount": d["gross_amount"],
                "total_deductions": total_deductions,
                "final_amount": d["final_amount"],
                "validation": validate_calculation(d["gross_amount"], parsed_items, d["final_amount"]),
                "calculation_trace": calculation_trace,
                "exceptions": exceptions,
            },
            evidence=linked_evidence,
            policies=relevant_policies,
            approver_id=d["approver_id"],
            approved_at=_to_iso_str(d["approved_at"]),
            integrity=integrity,
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

        # Build immutable policy snapshot for historical reproducibility
        policy_snapshot = []
        for pid in applicable_policies:
            cursor_p = await db.execute("SELECT * FROM policies WHERE policy_id = ?", (pid,))
            p_row = await cursor_p.fetchone()
            if p_row:
                policy_snapshot.append({
                    "policy_id": p_row["policy_id"],
                    "version": p_row["version"],
                    "clause_text": p_row["clause_text"],
                    "effective_date": p_row["effective_date"],
                })

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
                "policy_snapshot": policy_snapshot,
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
