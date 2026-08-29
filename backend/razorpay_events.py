"""Razorpay event storage — database persistence with tenant isolation.

Replaces in-memory event store with database-backed storage.
Each event is scoped to a tenant_id for multi-tenant isolation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from database import get_db

logger = logging.getLogger(__name__)


def compute_payload_hash(body_bytes: bytes) -> str:
    """Compute SHA-256 hash of the raw payload bytes for idempotency."""
    return hashlib.sha256(body_bytes).hexdigest()


async def store_event(
    raw_payload: dict,
    source: str = "local_simulator",
    verification_status: str = "unverified",
    payload_hash: str = "",
    tenant_id: str = "default",
) -> dict:
    """Normalize and store a Razorpay event.

    Returns the normalized event dict.
    """
    event_id = raw_payload.get("id", f"evt_{int(time.time() * 1000)}")
    event_type = raw_payload.get("event", "unknown")
    created_at = raw_payload.get("created_at", int(time.time()))

    # Extract entity info from payload
    payload_entities = raw_payload.get("payload", {})
    entity_type = "unknown"
    entity_id = ""
    amount = None
    currency = "INR"
    status = "unknown"
    payment_id = ""
    order_id = ""

    for key in ("payment", "order", "refund", "settlement"):
        if key in payload_entities:
            entity_data = payload_entities[key].get("entity", {})
            entity_type = key
            entity_id = entity_data.get("id", "")
            amount = entity_data.get("amount")
            currency = entity_data.get("currency", "INR")
            status = entity_data.get("status", "unknown")
            if key == "payment":
                payment_id = entity_data.get("id", "")
                order_id = entity_data.get("order_id", "")
            elif key == "order":
                order_id = entity_data.get("id", "")
            break

    # Extract facts deterministically
    extracted_facts = []
    extracted_facts.append({"fact": f"Razorpay event: {event_type} (source: {source})", "confidence": 1.0})
    if verification_status == "verified":
        extracted_facts.append({"fact": "Event signature verified by HMAC-SHA256", "confidence": 1.0})
    if payment_id:
        extracted_facts.append({"fact": f"Payment ID: {payment_id}", "confidence": 1.0})
    if amount is not None:
        extracted_facts.append({"fact": f"Amount: {amount} {currency}", "confidence": 1.0})
    if status:
        extracted_facts.append({"fact": f"Status: {status}", "confidence": 1.0})
    if order_id:
        extracted_facts.append({"fact": f"Order: {order_id}", "confidence": 1.0})

    now = datetime.now(timezone.utc).isoformat()
    event_ts = datetime.fromtimestamp(created_at, tz=timezone.utc).isoformat() if isinstance(created_at, (int, float)) else str(created_at)

    db = await get_db()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO razorpay_events "
            "(event_id, tenant_id, event_type, source, verification_status, razorpay_entity_type, "
            "razorpay_entity_id, payment_id, order_id, amount, currency, status, event_timestamp, "
            "received_at, extracted_facts, linked_decision_id, payload_hash, raw_payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, tenant_id, event_type, source, verification_status,
                entity_type, entity_id, payment_id, order_id,
                amount, currency, status,
                event_ts, now,
                json.dumps(extracted_facts), None, payload_hash,
                json.dumps(raw_payload),
            ),
        )
        await db.commit()
    finally:
        await db.close()

    return {
        "event_id": event_id,
        "event_type": event_type,
        "razorpay_entity_type": entity_type,
        "razorpay_entity_id": entity_id,
        "payment_id": payment_id,
        "order_id": order_id,
        "amount": amount,
        "currency": currency,
        "status": status,
        "source": source,
        "verification_status": verification_status,
        "event_timestamp": event_ts,
        "received_at": now,
        "extracted_facts": extracted_facts,
        "linked_decision_id": None,
    }


async def get_event_by_id(event_id: str, tenant_id: str = "default", db=None) -> Optional[dict]:
    own_db = db is None
    if own_db:
        db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM razorpay_events WHERE event_id = ? AND tenant_id = ?",
            (event_id, tenant_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        if own_db:
            await db.close()


async def get_event_by_payload_hash(payload_hash: str, tenant_id: str = "default", db=None) -> Optional[dict]:
    own_db = db is None
    if own_db:
        db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM razorpay_events WHERE payload_hash = ? AND tenant_id = ?",
            (payload_hash, tenant_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        if own_db:
            await db.close()


async def get_all_events(tenant_id: str = "default") -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM razorpay_events WHERE tenant_id = ? ORDER BY received_at DESC",
            (tenant_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def link_event_to_decision(event_id: str, decision_id: str, tenant_id: str = "default", db=None):
    """Link an event to a decision. If db is provided, uses the existing connection."""
    own_db = db is None
    if own_db:
        db = await get_db()
    try:
        await db.execute(
            "UPDATE razorpay_events SET linked_decision_id = ? WHERE event_id = ? AND tenant_id = ?",
            (decision_id, event_id, tenant_id),
        )
        if own_db:
            await db.commit()
    finally:
        if own_db:
            await db.close()
