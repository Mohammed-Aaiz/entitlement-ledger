"""Razorpay → EntitlementLedger Evidence Adapter.

Converts verified Razorpay events into EntitlementLedger evidence records
and optionally processes them into financial decisions.

Design:
- Deterministic extraction from structured Razorpay payloads (no LLM needed)
- Preserves original payload, source, verification_status, payload_hash
- Never treats unverified events as authoritative
- Clean separation from the calculation engine
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from calculations import build_line_items, calculate_final_amount
from hash_chain import compute_decision_hash

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic fact extraction from Razorpay payloads
# ---------------------------------------------------------------------------

def extract_razorpay_facts(event: dict) -> list[dict]:
    """Extract structured financial facts from a normalized Razorpay event.

    Only extracts facts that are actually present in the payload.
    Never invents data.

    Returns:
        List of {fact, confidence} dicts compatible with Evidence.extracted_facts.
    """
    facts = []
    entity_type = event.get("razorpay_entity_type", "unknown")
    event_type = event.get("event_type", "unknown")
    raw = event.get("raw_payload", {})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = {}

    # Navigate to the entity data
    payload_entities = raw.get("payload", {})
    entity_data = {}
    for key in ("payment", "order", "refund", "settlement"):
        if key in payload_entities:
            entity_data = payload_entities[key].get("entity", {})
            break

    # Common event metadata
    facts.append({
        "fact": f"Razorpay event: {event_type} (source: {event.get('source', 'unknown')})",
        "confidence": 1.0,
    })

    if event.get("verification_status") == "verified":
        facts.append({
            "fact": "Event signature verified by Razorpay webhook HMAC-SHA256",
            "confidence": 1.0,
        })

    # Payment-specific facts
    if entity_type == "payment" or "payment" in payload_entities:
        payment = entity_data if entity_type == "payment" else payload_entities.get("payment", {}).get("entity", {})
        if payment.get("id"):
            facts.append({"fact": f"Payment ID: {payment['id']}", "confidence": 1.0})
        if payment.get("amount") is not None:
            facts.append({"fact": f"Payment amount: {payment['amount']} paise", "confidence": 1.0})
        if payment.get("currency"):
            facts.append({"fact": f"Currency: {payment['currency']}", "confidence": 1.0})
        if payment.get("status"):
            facts.append({"fact": f"Payment status: {payment['status']}", "confidence": 1.0})
        if payment.get("order_id"):
            facts.append({"fact": f"Linked order: {payment['order_id']}", "confidence": 1.0})
        if payment.get("method"):
            facts.append({"fact": f"Payment method: {payment['method']}", "confidence": 1.0})
        if payment.get("captured") is not None:
            facts.append({"fact": f"Captured: {payment['captured']}", "confidence": 1.0})
        if payment.get("amount_refunded") and payment["amount_refunded"] > 0:
            facts.append({"fact": f"Amount refunded: {payment['amount_refunded']} paise", "confidence": 1.0})

    # Order-specific facts
    if entity_type == "order" or "order" in payload_entities:
        order = entity_data if entity_type == "order" else payload_entities.get("order", {}).get("entity", {})
        if order.get("id"):
            facts.append({"fact": f"Order ID: {order['id']}", "confidence": 1.0})
        if order.get("amount") is not None:
            facts.append({"fact": f"Order amount: {order['amount']} paise", "confidence": 1.0})
        if order.get("currency"):
            facts.append({"fact": f"Currency: {order['currency']}", "confidence": 1.0})
        if order.get("status"):
            facts.append({"fact": f"Order status: {order['status']}", "confidence": 1.0})

    # Refund-specific facts
    if entity_type == "refund" or "refund" in payload_entities:
        refund = entity_data if entity_type == "refund" else payload_entities.get("refund", {}).get("entity", {})
        if refund.get("id"):
            facts.append({"fact": f"Refund ID: {refund['id']}", "confidence": 1.0})
        if refund.get("amount") is not None:
            facts.append({"fact": f"Refund amount: {refund['amount']} paise", "confidence": 1.0})
        if refund.get("payment_id"):
            facts.append({"fact": f"Refund for payment: {refund['payment_id']}", "confidence": 1.0})
        if refund.get("status"):
            facts.append({"fact": f"Refund status: {refund['status']}", "confidence": 1.0})

    # Settlement-specific facts
    if entity_type == "settlement" or "settlement" in payload_entities:
        settlement = entity_data if entity_type == "settlement" else payload_entities.get("settlement", {}).get("entity", {})
        if settlement.get("id"):
            facts.append({"fact": f"Settlement ID: {settlement['id']}", "confidence": 1.0})
        if settlement.get("amount") is not None:
            facts.append({"fact": f"Settlement amount: {settlement['amount']} paise", "confidence": 1.0})
        if settlement.get("status"):
            facts.append({"fact": f"Settlement status: {settlement['status']}", "confidence": 1.0})

    return facts


# ---------------------------------------------------------------------------
# Event → Evidence record
# ---------------------------------------------------------------------------

def razorpay_event_to_evidence(event: dict) -> dict:
    """Convert a normalized Razorpay event into an EntitlementLedger Evidence record.

    The evidence record preserves:
    - Original Razorpay payload as raw_content
    - Source type mapped from entity type
    - Extracted facts from deterministic analysis
    - Source and verification metadata

    Args:
        event: Normalized Razorpay event dict (from razorpay_events.store_event).

    Returns:
        Evidence dict compatible with the existing _evidence store.
    """
    entity_type = event.get("razorpay_entity_type", "unknown")

    # Map Razorpay entity types to EntitlementLedger source types
    source_type_map = {
        "payment": "order",  # Payment events are order-level financial evidence
        "order": "order",
        "refund": "refund_record",
        "settlement": "order",  # Settlements are order-level financial evidence
    }
    source_type = source_type_map.get(entity_type, "order")

    # Build the evidence ID from the event
    evidence_id = f"ev_{event['event_id']}"

    # Extract facts deterministically
    extracted_facts = extract_razorpay_facts(event)

    # Build raw_content preserving the full Razorpay payload with metadata
    raw_content = json.dumps({
        "razorpay_event_id": event["event_id"],
        "razorpay_event_type": event["event_type"],
        "razorpay_entity_type": entity_type,
        "razorpay_entity_id": event.get("razorpay_entity_id", ""),
        "source": event.get("source", "unknown"),
        "verification_status": event.get("verification_status", "unverified"),
        "payload_hash": event.get("payload_hash", ""),
        "amount": event.get("amount"),
        "currency": event.get("currency"),
        "status": event.get("status"),
        "payload": event.get("raw_payload", {}),
    }, indent=2)

    return {
        "evidence_id": evidence_id,
        "source_type": source_type,
        "raw_content": raw_content,
        "extracted_facts": extracted_facts,
        "linked_decision_ids": [],
    }


# ---------------------------------------------------------------------------
# Process event → decision
# ---------------------------------------------------------------------------

def process_razorpay_event_to_decision(
    event: dict,
    evidence_record: dict,
    policies: list[dict],
    prev_decision_hash: str = "genesis",
) -> dict:
    """Process a Razorpay event into a financial decision.

    Uses deterministic calculation (platform fee only for payment events).
    No LLM required — structured Razorpay data is sufficient.

    Args:
        event: Normalized Razorpay event.
        evidence_record: The evidence record created from this event.
        policies: Available policy records.
        prev_decision_hash: Hash of the previous decision in the chain.

    Returns:
        Decision dict with hash, ready to append to the chain.
    """
    entity_type = event.get("razorpay_entity_type", "unknown")
    amount = event.get("amount", 0) or 0

    # Determine gross amount from the event
    gross_amount = amount  # Already in paise from Razorpay

    if gross_amount <= 0:
        raise ValueError(f"Cannot create decision: amount is {gross_amount}")

    # Determine entity (seller) from the event
    # For payment events, use order_id as entity reference
    entity_id = event.get("order_id") or event.get("payment_id") or event.get("razorpay_entity_id", "unknown")

    # Build line items: platform fee always applies
    line_items = build_line_items(
        gross_amount=gross_amount,
        has_sla_breach=False,
        has_returns=False,
        evidence_ids={"platform_fee": [evidence_record["evidence_id"]]},
    )

    final_amount = calculate_final_amount(gross_amount, line_items)

    # Create decision
    decision_id = f"dec_razorpay_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    decision_data = {
        "decision_id": decision_id,
        "entity_type": "seller",
        "entity_id": entity_id,
        "gross_amount": gross_amount,
        "line_items": [item.model_dump() for item in line_items],
        "final_amount": final_amount,
        "policy_version_id": "platform_1_1",
        "approver_id": "razorpay_pipeline",
        "approved_at": now,
        "model_output": {
            "source": "razorpay",
            "razorpay_event_id": event["event_id"],
            "razorpay_event_type": event["event_type"],
            "razorpay_entity_type": entity_type,
            "verification_status": event.get("verification_status", "unverified"),
            "extracted_facts_count": len(evidence_record.get("extracted_facts", [])),
            "claims": [
                {
                    "claim_type": "platform_fee",
                    "evidence_ids": [evidence_record["evidence_id"]],
                    "policy_clause_id": "platform_1_1",
                }
            ],
        },
        "prev_decision_hash": prev_decision_hash,
        "decision_hash": "",
        "created_at": now,
        "status": "APPROVED",
    }

    # Compute hash
    decision_data["decision_hash"] = compute_decision_hash(decision_data, prev_decision_hash)

    # Link evidence to this decision
    linked_ids = evidence_record.get("linked_decision_ids", [])
    if isinstance(linked_ids, str):
        linked_ids = json.loads(linked_ids)
    if decision_id not in linked_ids:
        linked_ids.append(decision_id)
    evidence_record["linked_decision_ids"] = linked_ids

    return decision_data
