"""AI pipeline orchestrator.

Coordinates the full flow:
Evidence → Extraction → Reasoning → Deterministic Calculation → Decision → Hash Chain
"""
import hashlib
import os
import logging
import time
import uuid
import json
from datetime import datetime
from typing import Optional

from models import LineItem
from calculations import (
    build_line_items, calculate_final_amount, validate_calculation,
    build_calculation_trace,
    EXCEPTION_MISSING_EVIDENCE, EXCEPTION_LOW_CONFIDENCE,
    EXCEPTION_CONFLICTING_EVIDENCE, EXCEPTION_DATA_INCONSISTENCY,
)
from hash_chain import compute_decision_hash
from ai.extraction import extract_facts_from_evidence
from ai.reasoning import reason_about_claims
from ai.llm_provider import is_ai_available

logger = logging.getLogger(__name__)


def compute_analysis_fingerprint(
    tenant_id: str,
    scenario_id: str,
    evidence_ids: list[str],
    policy_ids: list[str],
) -> str:
    """Compute a deterministic SHA-256 fingerprint for an analysis request.

    The fingerprint uniquely identifies the combination of:
      tenant_id + scenario_id + sorted evidence IDs + sorted policy IDs

    This is used for idempotency: two identical fingerprints mean the
    same analysis was already performed.
    """
    canonical = json.dumps(
        {
            "tenant_id": tenant_id,
            "scenario_id": scenario_id,
            "evidence_ids": sorted(evidence_ids),
            "policy_ids": sorted(policy_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _map_claims_to_calculation_params(claims: list[dict]) -> dict:
    """Convert AI claims into deterministic calculation parameters.

    This is the critical bridge between AI reasoning and deterministic calculation.
    The AI determines WHAT happened; this function determines the amounts.
    """
    params = {
        "has_sla_breach": False,
        "sla_penalty_amount": 0,
        "has_returns": False,
        "return_reserve_amount": 0,
        "evidence_ids": {},
    }

    for claim in claims:
        claim_type = claim.get("claim_type", "")
        evidence_ids = claim.get("evidence_ids", [])

        if claim_type == "sla_breach":
            params["has_sla_breach"] = True
            # Fixed penalty per policy - NOT determined by AI
            params["sla_penalty_amount"] = 12000
            params["evidence_ids"]["sla_penalty"] = evidence_ids
        elif claim_type == "return_processed":
            params["has_returns"] = True
            # Return amount comes from evidence, not AI calculation
            # For MVP, we use the standard reserve amount
            params["return_reserve_amount"] = 5000
            params["evidence_ids"]["return_reserve"] = evidence_ids

    # Platform fee always has evidence
    if "platform_fee" not in params["evidence_ids"]:
        params["evidence_ids"]["platform_fee"] = []

    return params


def _validate_evidence_references(
    claims: list[dict],
    available_evidence_ids: list[str],
) -> list[str]:
    """Validate that all referenced evidence exists."""
    errors = []
    for claim in claims:
        for ev_id in claim.get("evidence_ids", []):
            if ev_id not in available_evidence_ids:
                errors.append(f"Claim references non-existent evidence: {ev_id}")
    return errors


def _validate_policy_references(
    claims: list[dict],
    available_policy_ids: list[str],
) -> list[str]:
    """Validate that all referenced policies exist."""
    errors = []
    for claim in claims:
        policy_id = claim.get("policy_clause_id", "")
        if policy_id and policy_id not in available_policy_ids:
            errors.append(f"Claim references non-existent policy: {policy_id}")
    return errors


def run_pipeline(
    scenario_id: str,
    evidence_records: list[dict],
    policy_records: list[dict],
    prev_decision_hash: str = "genesis",
    use_mock: bool = False,
    agent_result: dict | None = None,
) -> dict:
    """Execute the full AI pipeline for a scenario.

    Args:
        scenario_id: The scenario to process
        evidence_records: List of evidence dicts
        policy_records: List of policy dicts
        prev_decision_hash: Hash of previous decision in chain
        use_mock: If True, use mock extraction/reasoning (for tests only)
        agent_result: If provided, skip extraction+reasoning and use the
            Finance Controller Agent's structured analysis.  The agent's
            output must contain 'analysis' (claims, classification, etc.)
            and 'extracted_facts'.

    Returns:
        Complete decision dict with hash chain
    """
    start_time = time.time()
    stage_durations = {}

    available_evidence_ids = [ev["evidence_id"] for ev in evidence_records]
    available_policy_ids = [p["policy_id"] for p in policy_records]

    # ── Stages 1+2: Extraction + Reasoning ──
    # When an agent_result is provided, the Finance Controller Agent has
    # already performed evidence gathering, tool calls, and structured
    # reasoning.  We use its output directly — no redundant LLM calls.
    if agent_result is not None:
        stage_start = time.time()
        all_extracted_facts = agent_result.get("extracted_facts", [])
        analysis = agent_result["analysis"]
        reasoning_result = {
            "claims": analysis.get("claims", []),
            "classification": analysis.get("classification", "exception"),
            "confidence": analysis.get("confidence", 0.0),
            "reasoning_summary": analysis.get("reasoning_summary", ""),
        }
        # Merge agent state into stage durations for audit
        agent_state = agent_result.get("agent_state")
        if agent_state:
            stage_durations["agent"] = {
                "duration_ms": getattr(agent_state, "duration_ms", 0),
                "iterations": getattr(agent_state, "iteration_count", 0),
                "tool_calls": len(getattr(agent_state, "tools_called", [])),
                "stop_reason": getattr(agent_state, "stop_reason", "unknown"),
            }
        stage_durations["extraction"] = 0  # Agent subsumes extraction
        stage_durations["reasoning"] = 0   # Agent subsumes reasoning
    else:
        # Legacy path: extraction + reasoning via LLM (or mock)
        # Stage 1: Extract facts from each evidence document
        stage_start = time.time()
        all_extracted_facts = []
        for ev in evidence_records:
            if use_mock:
                from ai.test_mocks import extract_facts_mock
                extracted = extract_facts_mock(
                    ev["evidence_id"],
                    ev["source_type"],
                    ev["raw_content"],
                )
            else:
                extracted = extract_facts_from_evidence(
                    ev["evidence_id"],
                    ev["source_type"],
                    ev["raw_content"],
                )

            # Update evidence record with extracted facts
            ev["extracted_facts"] = extracted["facts"]
            all_extracted_facts.extend([
                {**fact, "source_evidence_id": ev["evidence_id"]}
                for fact in extracted["facts"]
            ])

            logger.info(
                "Extracted %d facts from %s",
                len(extracted["facts"]),
                ev["evidence_id"],
            )
        stage_durations["extraction"] = time.time() - stage_start

        # Stage 2: Reason about claims
        stage_start = time.time()
        combined_facts = {"facts": all_extracted_facts}

        if use_mock:
            from ai.test_mocks import reason_about_claims_mock

            # Determine scenario type from evidence
            has_delivery = any(ev["source_type"] == "delivery" for ev in evidence_records)
            has_refund = any(ev["source_type"] == "refund_record" for ev in evidence_records)
            has_complaint = any(ev["source_type"] == "complaint" for ev in evidence_records)

            if has_refund and has_delivery:
                scenario_type = "clear"
            elif has_delivery:
                scenario_type = "sla_only"
            elif has_complaint:
                scenario_type = "no_penalty"
            else:
                scenario_type = "no_issues"

            reasoning_result = reason_about_claims_mock(combined_facts, policy_records, scenario_type)
        else:
            reasoning_result = reason_about_claims(combined_facts, policy_records)

        logger.info(
            "Reasoning result: classification=%s, claims=%d",
            reasoning_result["classification"],
            len(reasoning_result["claims"]),
        )
        stage_durations["reasoning"] = time.time() - stage_start

    # Stage 3: Validate references
    stage_start = time.time()
    evidence_errors = _validate_evidence_references(
        reasoning_result["claims"],
        available_evidence_ids,
    )
    policy_errors = _validate_policy_references(
        reasoning_result["claims"],
        available_policy_ids,
    )

    if evidence_errors or policy_errors:
        all_errors = evidence_errors + policy_errors
        logger.error("Reference validation failed: %s", all_errors)
        raise ValueError(f"Invalid references: {'; '.join(all_errors)}")
    stage_durations["validation"] = time.time() - stage_start

    # Stage 4: Deterministic calculation
    stage_start = time.time()

    # Determine gross amount from order evidence
    gross_amount = 0
    for ev in evidence_records:
        if ev["source_type"] == "order":
            try:
                content = json.loads(ev["raw_content"])
                gross_amount = content.get("amount", 0)
                break
            except (json.JSONDecodeError, KeyError):
                continue

    if gross_amount == 0:
        raise ValueError("Could not determine gross amount from order evidence")

    # Map claims to calculation parameters
    calc_params = _map_claims_to_calculation_params(reasoning_result["claims"])

    # Platform fee evidence comes from order
    calc_params["evidence_ids"]["platform_fee"] = [
        ev["evidence_id"] for ev in evidence_records if ev["source_type"] == "order"
    ]

    # Build deterministic line items
    line_items = build_line_items(
        gross_amount=gross_amount,
        has_sla_breach=calc_params["has_sla_breach"],
        sla_penalty_amount=calc_params["sla_penalty_amount"],
        has_returns=calc_params["has_returns"],
        return_reserve_amount=calc_params["return_reserve_amount"],
        evidence_ids=calc_params["evidence_ids"],
    )

    final_amount = calculate_final_amount(gross_amount, line_items)

    # Validate calculation
    validation = validate_calculation(gross_amount, line_items, final_amount)
    if not validation["valid"]:
        raise ValueError(
            f"Calculation validation failed: expected {validation['expected_final']}, "
            f"got {validation['calculated_final']}"
        )

    stage_durations["calculation"] = time.time() - stage_start

    # Build calculation trace for auditability
    calculation_trace = build_calculation_trace(
        gross_amount=gross_amount,
        line_items=line_items,
        final_amount=final_amount,
    )

    # Detect exceptions
    exceptions = _detect_exceptions(
        reasoning_result=reasoning_result,
        evidence_records=evidence_records,
        validation=validation,
        gross_amount=gross_amount,
        line_items=line_items,
    )

    # Build immutable policy snapshot for historical reproducibility.
    # Each decision captures the exact policy content that was in effect,
    # so that old decisions are never affected by future policy updates.
    policy_snapshot = [
        {
            "policy_id": p["policy_id"],
            "version": p["version"],
            "clause_text": p["clause_text"],
            "effective_date": p["effective_date"],
        }
        for p in policy_records
    ]

    # Stage 5: Create decision
    stage_start = time.time()
    decision_id = f"dec_{uuid.uuid4().hex[:8]}"
    now = datetime.now().isoformat()

    decision_data = {
        "decision_id": decision_id,
        "entity_type": "seller",
        "entity_id": _extract_seller_id(evidence_records),
        "gross_amount": gross_amount,
        "line_items": [item.model_dump() for item in line_items],
        "final_amount": final_amount,
        "policy_version_id": ",".join(available_policy_ids),
        "approver_id": "ai_pipeline",
        "approved_at": None,  # Not approved yet - REVIEW_REQUIRED
        "model_output": {
            "scenario_id": scenario_id,
            "analysis_fingerprint": compute_analysis_fingerprint(
                tenant_id="",  # tenant_id is set by routes.py after pipeline
                scenario_id=scenario_id,
                evidence_ids=available_evidence_ids,
                policy_ids=available_policy_ids,
            ),
            "claims": reasoning_result["claims"],
            "classification": reasoning_result["classification"],
            "confidence": reasoning_result["confidence"],
            "reasoning_summary": reasoning_result["reasoning_summary"],
            "extracted_facts_count": len(all_extracted_facts),
            "calculation_trace": calculation_trace,
            "exceptions": exceptions,
            "policy_snapshot": policy_snapshot,
        },
        "prev_decision_hash": prev_decision_hash,
        "decision_hash": "",
        "created_at": now,
        "status": "REVIEW_REQUIRED",  # AI decisions must be reviewed
    }

    # Compute hash BEFORE adding pipeline_stages (which is not persisted
    # to the DB and must not be part of the hash input).
    decision_data["decision_hash"] = compute_decision_hash(decision_data, prev_decision_hash)

    # Add pipeline_stages AFTER hashing — this is audit metadata only,
    # returned in the API response but not stored in the decisions table.
    decision_data["pipeline_stages"] = {
        "extraction": {"duration_ms": int(stage_durations["extraction"] * 1000)},
        "reasoning": {"duration_ms": int(stage_durations["reasoning"] * 1000)},
        "validation": {"duration_ms": int(stage_durations["validation"] * 1000)},
        "calculation": {"duration_ms": int(stage_durations["calculation"] * 1000)},
    }
    stage_durations["commit"] = time.time() - stage_start

    total_duration = time.time() - start_time
    logger.info(
        "Pipeline completed for %s in %.2fs (extraction=%.2fs, reasoning=%.2fs)",
        scenario_id,
        total_duration,
        stage_durations["extraction"],
        stage_durations["reasoning"],
    )

    # Link evidence to this decision
    for ev in evidence_records:
        linked_ids = ev.get("linked_decision_ids", [])
        if isinstance(linked_ids, str):
            linked_ids = json.loads(linked_ids)
        if decision_id not in linked_ids:
            linked_ids.append(decision_id)
        ev["linked_decision_ids"] = linked_ids

    return {
        "decision": decision_data,
        "evidence": evidence_records,
        "stages": stage_durations,
        "total_duration_ms": int(total_duration * 1000),
    }


def _detect_exceptions(
    reasoning_result: dict,
    evidence_records: list[dict],
    validation: dict,
    gross_amount: int,
    line_items: list,
) -> list[dict]:
    """Detect structured exceptions from the pipeline run.

    Returns a list of exception dicts, each with:
      - category: one of the EXCEPTION_* constants
      - message: human-readable explanation
      - severity: "warning" or "critical"
    """
    exceptions = []

    # LOW_CONFIDENCE: confidence below 0.6
    confidence = reasoning_result.get("confidence", 1.0)
    if confidence < 0.6:
        exceptions.append({
            "category": EXCEPTION_LOW_CONFIDENCE,
            "message": f"AI confidence is {confidence:.2f}, below 0.6 threshold",
            "severity": "warning",
        })

    # MISSING_EVIDENCE: no order evidence found
    has_order = any(ev["source_type"] == "order" for ev in evidence_records)
    if not has_order:
        exceptions.append({
            "category": EXCEPTION_MISSING_EVIDENCE,
            "message": "No order evidence found in the evidence set",
            "severity": "critical",
        })

    # CONFLICTING_EVIDENCE: multiple conflicting claims
    claims = reasoning_result.get("claims", [])
    claim_types = [c.get("type", "") for c in claims]
    if "sla_breach" in claim_types and "sla_met" in claim_types:
        exceptions.append({
            "category": EXCEPTION_CONFLICTING_EVIDENCE,
            "message": "Conflicting SLA breach and SLA met claims detected",
            "severity": "warning",
        })

    # DATA_INCONSISTENCY: gross amount is 0 or negative
    if gross_amount <= 0:
        exceptions.append({
            "category": EXCEPTION_DATA_INCONSISTENCY,
            "message": f"Gross amount is {gross_amount}, expected positive value",
            "severity": "critical",
        })

    # DATA_INCONSISTENCY: calculation validation failed
    if not validation.get("valid", True):
        exceptions.append({
            "category": EXCEPTION_DATA_INCONSISTENCY,
            "message": (
                f"Calculation mismatch: expected {validation.get('expected_final')}, "
                f"got {validation.get('calculated_final')}"
            ),
            "severity": "critical",
        })

    # POLICY_AMBIGUITY: no claims matched any policy
    if not claims and evidence_records:
        exceptions.append({
            "category": "POLICY_AMBIGUITY",
            "message": "No claims extracted despite evidence being present",
            "severity": "warning",
        })

    return exceptions


def _extract_seller_id(evidence_records: list[dict]) -> str:
    """Extract entity ID from evidence records.

    Tries in order:
    1. seller_id from order evidence
    2. razorpay_entity_id (e.g. order_TVtOb7uZcvSkvY) from any evidence
    3. "unknown" if nothing found
    """
    for ev in evidence_records:
        if ev["source_type"] == "order":
            try:
                content = json.loads(ev["raw_content"])
                # Prefer seller_id if present
                seller_id = content.get("seller_id")
                if seller_id:
                    return seller_id
                # Fall back to Razorpay entity ID (order ID, payment ID, etc.)
                entity_id = content.get("razorpay_entity_id", "")
                if entity_id:
                    return entity_id
            except (json.JSONDecodeError, KeyError):
                continue
    # Try any evidence source for razorpay_entity_id
    for ev in evidence_records:
        try:
            content = json.loads(ev["raw_content"])
            entity_id = content.get("razorpay_entity_id", "")
            if entity_id:
                return entity_id
        except (json.JSONDecodeError, KeyError):
            continue
    return "unknown"
