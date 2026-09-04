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
from ai.failure_taxonomy import FailureType

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


# ── Claim Mapping Table ──────────────────────────────────────────────
# Every claim_type maps to either a monetary effect or a non-monetary
# observation.  No claim silently disappears.
#
# claim_type           → monetary_effect  → calculation_rule
# ────────────────────── ───────────────── ──────────────────────────────
# platform_fee         → YES              → gross * 0.08 (8%)
# sla_breach           → YES              → fixed penalty 12000
# return_processed     → YES              → reserve 5000
# no_penalty           → NO               → recorded for audit only
# other                → NO               → recorded for audit only
# compliance_note      → NO               → recorded for audit only

CLAIM_MAPPING = {
    "platform_fee": {
        "monetary_effect": True,
        "calculation": "percentage",
        "rate": 0.08,
        "description": "Platform fee as percentage of gross amount",
    },
    "sla_breach": {
        "monetary_effect": True,
        "calculation": "fixed",
        "amount": 12000,
        "description": "Fixed penalty for SLA breach per policy SLA-4.2",
    },
    "return_processed": {
        "monetary_effect": True,
        "calculation": "fixed",
        "amount": 5000,
        "description": "Reserve withholding for processed returns per policy Returns-3.1",
    },
    "no_penalty": {
        "monetary_effect": False,
        "calculation": None,
        "amount": 0,
        "description": "No penalty applies — recorded for reasoning/audit",
    },
    "other": {
        "monetary_effect": False,
        "calculation": None,
        "amount": 0,
        "description": "Other observation — recorded for reasoning/audit",
    },
    "compliance_note": {
        "monetary_effect": False,
        "calculation": None,
        "amount": 0,
        "description": "Compliance observation — recorded for reasoning/audit",
    },
}


def _map_claims_to_calculation_params(claims: list[dict]) -> dict:
    """Convert AI claims into deterministic calculation parameters.

    This is the critical bridge between AI reasoning and deterministic calculation.
    The AI determines WHAT happened; this function determines the amounts.

    Every claim_type is handled explicitly.  Unknown claim types are logged
    and recorded but produce no monetary effect.
    """
    params = {
        "has_sla_breach": False,
        "sla_penalty_amount": 0,
        "has_returns": False,
        "return_reserve_amount": 0,
        "evidence_ids": {},
    }

    seen_types = set()
    for claim in claims:
        claim_type = claim.get("claim_type", "")
        evidence_ids = claim.get("evidence_ids", [])
        mapping = CLAIM_MAPPING.get(claim_type)

        if claim_type == "sla_breach":
            params["has_sla_breach"] = True
            # Fixed penalty per policy - NOT determined by AI
            params["sla_penalty_amount"] = CLAIM_MAPPING["sla_breach"]["amount"]
            params["evidence_ids"]["sla_penalty"] = evidence_ids
        elif claim_type == "return_processed":
            params["has_returns"] = True
            params["return_reserve_amount"] = CLAIM_MAPPING["return_processed"]["amount"]
            params["evidence_ids"]["return_reserve"] = evidence_ids
        elif claim_type == "platform_fee":
            # Platform fee always applies — evidence from order
            pass  # handled below
        elif mapping and not mapping["monetary_effect"]:
            # Non-monetary claim: recorded for audit, no financial effect
            seen_types.add(claim_type)
        else:
            logger.warning("Unknown claim_type: %s — no monetary effect applied", claim_type)

    # Platform fee always has evidence
    if "platform_fee" not in params["evidence_ids"]:
        params["evidence_ids"]["platform_fee"] = []

    return params


# ── Evidence Sufficiency States ──────────────────────────────────────
# Every case is classified into exactly one of these states.
# This is deterministic — no LLM confidence number overrides it.

SUFFICIENT = "SUFFICIENT"          # All required evidence present and consistent
INSUFFICIENT = "INSUFFICIENT"       # Required evidence missing
CONFLICTING = "CONFLICTING"         # Evidence present but contradictory
UNAVAILABLE = "UNAVAILABLE"         # Evidence cannot be retrieved


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


def _determine_evidence_sufficiency(
    reasoning_result: dict,
    evidence_records: list[dict],
    evidence_errors: list[str],
) -> str:
    """Determine evidence sufficiency state.

    This is deterministic — uses application-level checks, not AI confidence.

    States:
      SUFFICIENT: claims have evidence, no errors, no conflicts
      INSUFFICIENT: required evidence missing or claims have no evidence IDs
      CONFLICTING: agent reports conflicting evidence
      UNAVAILABLE: no evidence records at all
    """
    if not evidence_records:
        return UNAVAILABLE

    if evidence_errors:
        return INSUFFICIENT

    claims = reasoning_result.get("claims", [])

    # No claims despite evidence present = insufficient
    if not claims and evidence_records:
        return INSUFFICIENT

    # Agent-reported conflicts
    agent_conflicts = reasoning_result.get("conflicting_evidence", [])
    if agent_conflicts:
        return CONFLICTING

    # Check for structural contradictions in claims
    claim_types = [c.get("claim_type", "") for c in claims]
    for claim in claims:
        evidence_ids = set(claim.get("evidence_ids", []))
        for other in claims:
            if other is claim:
                continue
            other_ids = set(other.get("evidence_ids", []))
            if evidence_ids & other_ids:
                # Same evidence supports different claims
                # Only flag if the claim types are inherently contradictory
                if _are_contradictory(claim.get("claim_type", ""), other.get("claim_type", "")):
                    return CONFLICTING

    # Check if claims have empty evidence lists
    for claim in claims:
        if not claim.get("evidence_ids"):
            return INSUFFICIENT

    return SUFFICIENT


def _are_contradictory(type_a: str, type_b: str) -> bool:
    """Determine if two claim types are inherently contradictory."""
    # These pairs are genuinely contradictory
    contradictory_pairs = {
        frozenset(("sla_breach", "no_penalty")),
    }
    return frozenset((type_a, type_b)) in contradictory_pairs


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


# ── Policy Outcome Contract ───────────────────────────────────────
# Every claim must have a deterministic policy outcome.
# The LLM suggests which policy applies; the application verifies
# the policy conditions are actually satisfied by the evidence.

POLICY_SATISFIED = "SATISFIED"      # Evidence proves policy conditions met
POLICY_VIOLATED = "VIOLATED"        # Evidence proves policy conditions NOT met
POLICY_INAPPLICABLE = "INAPPLICABLE"  # Policy does not apply to this case
POLICY_UNRESOLVED = "UNRESOLVED"    # Cannot determine policy outcome


def _evaluate_policy_outcomes(
    claims: list[dict],
    evidence_records: list[dict],
    policy_records: list[dict],
) -> dict:
    """Deterministically evaluate policy outcomes for each claim.

    For each claim, checks whether the referenced policy conditions
    are actually satisfied by the cited evidence.  This is a purely
    deterministic check — no LLM involved.

    Returns a dict mapping claim index → policy outcome dict:
      {"policy_clause_id": str, "outcome": str, "reason": str}
    """
    # Build lookup: evidence_id → evidence record
    ev_by_id = {ev["evidence_id"]: ev for ev in evidence_records}

    # Build lookup: policy_id → policy record
    pol_by_id = {p["policy_id"]: p for p in policy_records}

    outcomes = []
    for i, claim in enumerate(claims):
        policy_id = claim.get("policy_clause_id", "")
        claim_type = claim.get("claim_type", "")
        evidence_ids = claim.get("evidence_ids", [])

        policy = pol_by_id.get(policy_id)
        if not policy:
            outcomes.append({
                "policy_clause_id": policy_id,
                "outcome": POLICY_UNRESOLVED,
                "reason": f"Policy {policy_id} not found in available policies",
            })
            continue

        # Gather the actual evidence records referenced by this claim
        claim_evidence = [ev_by_id[eid] for eid in evidence_ids if eid in ev_by_id]

        if not claim_evidence:
            outcomes.append({
                "policy_clause_id": policy_id,
                "outcome": POLICY_UNRESOLVED,
                "reason": f"No valid evidence for claim referencing {policy_id}",
            })
            continue

        # Evaluate based on claim_type
        outcome = _evaluate_claim_policy(claim_type, claim_evidence, policy)
        outcome["policy_clause_id"] = policy_id
        outcomes.append(outcome)

    return {"outcomes": outcomes}


def _evaluate_claim_policy(
    claim_type: str,
    evidence_records: list[dict],
    policy: dict,
) -> dict:
    """Evaluate whether a specific claim satisfies its policy conditions.

    Uses ONLY deterministic checks on actual evidence content.
    """
    if claim_type == "platform_fee":
        # Platform fee always applies to completed transactions.
        # Evidence must contain an order with a positive amount.
        for ev in evidence_records:
            if ev.get("source_type") == "order":
                try:
                    content = json.loads(ev.get("raw_content", "{}"))
                    amount = content.get("amount", 0)
                    if amount > 0:
                        return {
                            "outcome": POLICY_SATISFIED,
                            "reason": f"Order {content.get('order_id', '?')} has amount {amount} — platform fee applies",
                        }
                except (json.JSONDecodeError, KeyError):
                    continue
        return {
            "outcome": POLICY_VIOLATED,
            "reason": "No order evidence with positive amount found",
        }

    elif claim_type == "sla_breach":
        # SLA breach requires delivery evidence showing delay >= 3 business days.
        for ev in evidence_records:
            if ev.get("source_type") == "delivery":
                try:
                    content = json.loads(ev.get("raw_content", "{}"))
                    delay = content.get("delay_days", 0)
                    if delay is None:
                        delay = 0
                    if delay >= 3:
                        return {
                            "outcome": POLICY_SATISFIED,
                            "reason": f"Delivery delay {delay} days >= 3 — SLA breach confirmed",
                        }
                    else:
                        return {
                            "outcome": POLICY_VIOLATED,
                            "reason": f"Delivery delay {delay} days < 3 — SLA breach NOT triggered",
                        }
                except (json.JSONDecodeError, KeyError):
                    continue
        return {
            "outcome": POLICY_UNRESOLVED,
            "reason": "No delivery evidence found to evaluate SLA breach",
        }

    elif claim_type == "return_processed":
        # Return processed requires refund evidence with status "processed".
        for ev in evidence_records:
            if ev.get("source_type") == "refund_record":
                try:
                    content = json.loads(ev.get("raw_content", "{}"))
                    status = content.get("status", "")
                    if status == "processed":
                        return {
                            "outcome": POLICY_SATISFIED,
                            "reason": f"Return {content.get('refund_id', '?')} is processed — reserve applies",
                        }
                    else:
                        return {
                            "outcome": POLICY_VIOLATED,
                            "reason": f"Return status is '{status}', not 'processed' — reserve NOT triggered",
                        }
                except (json.JSONDecodeError, KeyError):
                    continue
        return {
            "outcome": POLICY_UNRESOLVED,
            "reason": "No refund evidence found to evaluate return processed",
        }

    elif claim_type in ("no_penalty", "other", "compliance_note"):
        # Non-monetary claims: policy is satisfied if the claim exists
        # and evidence supports the observation.
        return {
            "outcome": POLICY_SATISFIED,
            "reason": f"Non-monetary claim '{claim_type}' — recorded for audit",
        }

    else:
        # Unknown claim type: cannot evaluate policy conditions
        return {
            "outcome": POLICY_UNRESOLVED,
            "reason": f"Cannot evaluate policy for unknown claim type '{claim_type}'",
        }


def _determine_decision_status(
    agent_success: bool,
    evidence_sufficiency: str,
    evidence_errors: list[str],
    policy_errors: list[str],
    classification: str,
    exceptions: list[dict],
    validation: dict,
    policy_outcomes: dict | None = None,
    gemini_needed: bool = False,
    gemini_available: bool = False,
) -> str:
    """Deterministic approval gate.

    Returns APPROVED only when ALL conditions are satisfied.
    Otherwise returns REVIEW_REQUIRED.

    The LLM never directly sets approval.  This function uses only
    deterministic application-level checks — no confidence thresholds,
    no LLM output beyond what was already validated.

    Conditions for APPROVED:
      1. Agent execution succeeded (no provider/tool failures)
      2. Evidence sufficiency == SUFFICIENT
      3. No evidence reference validation errors
      4. No policy reference validation errors
      5. Classification is not "exception"
      6. No critical-severity exceptions
      7. Deterministic calculation passed validation
      8. All policy outcomes are SATISFIED (no VIOLATED/UNRESOLVED)
      9. If deep evidence analysis was needed, Gemini must have responded
    """
    # Condition 1: Agent must have succeeded
    if not agent_success:
        logger.info("Approval gate: REJECTED — agent execution failed")
        return "REVIEW_REQUIRED"

    # Condition 2: Evidence must be sufficient
    if evidence_sufficiency != SUFFICIENT:
        logger.info(
            "Approval gate: REJECTED — evidence sufficiency is %s",
            evidence_sufficiency,
        )
        return "REVIEW_REQUIRED"

    # Condition 3: No evidence reference errors
    if evidence_errors:
        logger.info(
            "Approval gate: REJECTED — %d evidence reference errors",
            len(evidence_errors),
        )
        return "REVIEW_REQUIRED"

    # Condition 4: No policy reference errors
    if policy_errors:
        logger.info(
            "Approval gate: REJECTED — %d policy reference errors",
            len(policy_errors),
        )
        return "REVIEW_REQUIRED"

    # Condition 5: Classification must not be exception
    if classification == "exception":
        logger.info("Approval gate: REJECTED — classification is exception")
        return "REVIEW_REQUIRED"

    # Condition 6: No critical-severity exceptions
    critical_exceptions = [e for e in exceptions if e.get("severity") == "critical"]
    if critical_exceptions:
        logger.info(
            "Approval gate: REJECTED — %d critical exceptions",
            len(critical_exceptions),
        )
        return "REVIEW_REQUIRED"

    # Condition 7: Calculation must be valid
    if not validation.get("valid", False):
        logger.info("Approval gate: REJECTED — calculation validation failed")
        return "REVIEW_REQUIRED"

    # Condition 8: All policy outcomes must be SATISFIED
    if policy_outcomes is not None:
        for outcome in policy_outcomes.get("outcomes", []):
            if outcome.get("outcome") == POLICY_VIOLATED:
                logger.info(
                    "Approval gate: REJECTED — policy %s VIOLATED: %s",
                    outcome.get("policy_clause_id"),
                    outcome.get("reason"),
                )
                return "REVIEW_REQUIRED"
            if outcome.get("outcome") == POLICY_UNRESOLVED:
                logger.info(
                    "Approval gate: REJECTED — policy %s UNRESOLVED: %s",
                    outcome.get("policy_clause_id"),
                    outcome.get("reason"),
                )
                return "REVIEW_REQUIRED"

    # Condition 9: If deep evidence analysis was needed, Gemini must have responded
    if gemini_needed and not gemini_available:
        logger.info(
            "Approval gate: REJECTED — deep evidence analysis needed but Gemini unavailable"
        )
        return "REVIEW_REQUIRED"

    # All conditions satisfied
    logger.info("Approval gate: APPROVED — all deterministic conditions met")
    return "APPROVED"


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
        # Record as exception rather than hard failure — REVIEW_REQUIRED
        # This preserves idempotency and avoids losing the analysis
        reasoning_result.setdefault("missing_evidence", []).extend(all_errors)
        reasoning_result["classification"] = "exception"
        reasoning_result["confidence"] = 0.0
        reasoning_result["reasoning_summary"] += f" Reference errors: {'; '.join(all_errors)}"
    stage_durations["validation"] = time.time() - stage_start

    # Stage 3b: Determine evidence sufficiency (deterministic)
    evidence_sufficiency = _determine_evidence_sufficiency(
        reasoning_result, evidence_records, evidence_errors,
    )

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

    # Stage 5a: Evaluate policy outcomes (deterministic)
    policy_outcomes = _evaluate_policy_outcomes(
        reasoning_result["claims"],
        evidence_records,
        policy_records,
    )

    # Stage 5b: Deterministic approval gate
    # The LLM determines WHAT happened; this gate determines IF it is
    # safe to auto-approve.  ALL conditions must be met — no exceptions.
    # The LLM never directly sets approval or monetary amounts.
    agent_success = True
    gemini_needed = False
    gemini_available = False
    if agent_result is not None:
        agent_state_obj = agent_result.get("agent_state")
        agent_success = getattr(agent_state_obj, "success", False) if agent_state_obj else False
        gemini_needed = agent_result.get("gemini_needed", False)
        gemini_available = agent_result.get("gemini_available", False)

    approval_status = _determine_decision_status(
        agent_success=agent_success,
        evidence_sufficiency=evidence_sufficiency,
        evidence_errors=evidence_errors,
        policy_errors=policy_errors,
        classification=reasoning_result["classification"],
        exceptions=exceptions,
        validation=validation,
        policy_outcomes=policy_outcomes,
        gemini_needed=gemini_needed,
        gemini_available=gemini_available,
    )

    # Stage 6: Create decision
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
        "approved_at": now if approval_status == "APPROVED" else None,
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
            "evidence_sufficiency": evidence_sufficiency,
            "extracted_facts_count": len(all_extracted_facts),
            "calculation_trace": calculation_trace,
            "exceptions": exceptions,
            "policy_snapshot": policy_snapshot,
            "policy_outcomes": policy_outcomes.get("outcomes", []) if policy_outcomes else [],
        },
        "prev_decision_hash": prev_decision_hash,
        "decision_hash": "",
        "created_at": now,
        "status": approval_status,
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
    # 1. Structural check: same evidence supports contradictory claims
    claims = reasoning_result.get("claims", [])
    claim_types = [c.get("claim_type", "") for c in claims]
    if "sla_breach" in claim_types and "return_processed" in claim_types:
        # Not inherently conflicting — both can be true.  Only flag if
        # the same evidence IDs appear in both (true contradiction).
        breach_ids = set()
        return_ids = set()
        for c in claims:
            ct = c.get("claim_type", "")
            eids = set(c.get("evidence_ids", []))
            if ct == "sla_breach":
                breach_ids |= eids
            elif ct == "return_processed":
                return_ids |= eids
        if breach_ids & return_ids:
            exceptions.append({
                "category": EXCEPTION_CONFLICTING_EVIDENCE,
                "message": (
                    "Same evidence supports both SLA breach and return "
                    "processed claims — possible contradiction"
                ),
                "severity": "warning",
            })

    # 2. Agent-reported conflicts (from the LLM's own analysis)
    agent_conflicts = reasoning_result.get("conflicting_evidence", [])
    if agent_conflicts:
        exceptions.append({
            "category": EXCEPTION_CONFLICTING_EVIDENCE,
            "message": f"Agent reported {len(agent_conflicts)} conflicting evidence(s)",
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
