"""Razorpay integration routes — production persistence with tenant isolation.

All endpoints require authentication. Events are scoped to the user's tenant_id.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import razorpay_client
import razorpay_events
import razorpay_registry
from auth import CurrentUser, get_current_user
from database import get_db, log_audit
from models import RazorpayEventResponse, RazorpayConnectionInfo

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_PAYLOAD_BYTES = 1_048_576

# Maximum records to fetch per sync operation
MAX_SYNC_RECORDS = 100

SUPPORTED_EVENT_TYPES = {
    "payment.authorized", "payment.captured", "payment.failed",
    "order.paid", "refund.created", "refund.processed", "settlement.processed",
}


def _safe_int(value) -> int:
    """Coerce a Razorpay recon numeric field to int (paise), never raising."""
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _recon_rows_from_payload(settlement_id: str, payload: dict) -> list[dict]:
    """Extract deterministic settlement↔payment linkage rows from a Razorpay
    settlement recon response.

    Both the single-settlement recon (``GET /settlements/{id}/recon``) and
    the combined recon (``GET /settlements/recon/combined``) return a
    ``collection`` whose items carry: ``type``/``entity``, ``entity_id``,
    ``payment_id``, ``order_id``, ``amount``, ``fee``, ``tax``.

    Only financially meaningful, linkable rows are kept:
    - ``payment`` items link a settlement to the payment via ``entity_id``.
    - ``refund`` items link a settlement to the refunded payment.
    - transfer/adjustment/dispute rows are a separate financial context and
      are NOT attached to a payment as settlement evidence.
    A row with neither payment nor order reference is skipped — the
    relationship is never guessed.
    """
    if not isinstance(payload, dict):
        return []
    items = payload.get("items", []) or []
    rows: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rtype = (item.get("type") or item.get("entity") or "").lower()
        entity_id = item.get("entity_id") or ""
        pid = item.get("payment_id") or ""
        oid = item.get("order_id") or ""
        # A payment-type row identifies the payment by entity_id.
        if rtype == "payment" and entity_id.startswith("pay_") and not pid:
            pid = entity_id
        if rtype not in ("payment", "refund"):
            continue
        if not pid and not oid:
            continue
        key = pid or oid or entity_id
        rows.append({
            "recon_id": f"{settlement_id}:{rtype}:{key}",
            "settlement_id": settlement_id,
            "payment_id": pid or "",
            "order_id": oid or "",
            "amount": _safe_int(item.get("amount")),
            "fee": _safe_int(item.get("fee")),
            "tax": _safe_int(item.get("tax")),
            "recon_type": rtype,
        })
    return rows


class AccountMappingRequest(BaseModel):
    account_id: str
    tenant_id: str
    webhook_secret: Optional[str] = None


@router.post("/razorpay/accounts")
async def register_account_mapping(
    req: AccountMappingRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Register a Razorpay account_id -> tenant_id mapping.

    Only admins can register account mappings. This enables webhook
    tenant resolution: when a webhook arrives with account_id=xxx,
    the system looks up which tenant owns that Razorpay account.
    """
    if user.role != "admin":
        raise HTTPException(403, "Only admins can register account mappings")

    db = await get_db()
    try:
        # Verify the tenant exists
        cursor = await db.execute("SELECT tenant_id FROM tenants WHERE tenant_id = ?", (req.tenant_id,))
        if not await cursor.fetchone():
            raise HTTPException(404, f"Tenant '{req.tenant_id}' not found")

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO razorpay_account_mappings (account_id, tenant_id, webhook_secret, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(account_id) DO UPDATE SET tenant_id=excluded.tenant_id, webhook_secret=excluded.webhook_secret, updated_at=excluded.updated_at",
            (req.account_id, req.tenant_id, req.webhook_secret, now, now),
        )
        await db.commit()

        await log_audit(user.tenant_id, "razorpay.account_mapped", "razorpay_account", req.account_id,
                        user_id=user.user_id, details={"account_id": req.account_id, "tenant_id": req.tenant_id})

        return {"status": "mapped", "account_id": req.account_id, "tenant_id": req.tenant_id}
    finally:
        await db.close()


@router.get("/razorpay/accounts")
async def list_account_mappings(user: CurrentUser = Depends(get_current_user)):
    """List all Razorpay account-to-tenant mappings for this tenant."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT account_id, tenant_id, created_at, updated_at FROM razorpay_account_mappings WHERE tenant_id = ?",
            (user.tenant_id,),
        )
        rows = await cursor.fetchall()
        return {"mappings": [dict(r) for r in rows]}
    finally:
        await db.close()


async def _resolve_tenant_from_webhook(account_id: str) -> Optional[str]:
    """Look up the tenant_id for a Razorpay account_id.

    Returns the tenant_id if a mapping exists, None otherwise.
    """
    if not account_id:
        return None
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT tenant_id FROM razorpay_account_mappings WHERE account_id = ?",
            (account_id,),
        )
        row = await cursor.fetchone()
        return row["tenant_id"] if row else None
    finally:
        await db.close()


@router.post("/webhooks/razorpay")
async def receive_webhook(request: Request):
    """Receive and verify a Razorpay webhook event.

    Security flow:
    1. Verify HMAC-SHA256 signature over raw bytes (constant-time).
    2. Extract account_id from the webhook payload.
    3. Resolve tenant from the razorpay_account_mappings table.
    4. If no mapping found, reject the webhook (quarantine unknown merchants).
    5. Store the event under the resolved tenant.
    6. Idempotent via payload_hash dedup.
    """
    body_bytes = await request.body()

    if len(body_bytes) > MAX_PAYLOAD_BYTES:
        raise HTTPException(413, "Payload too large")

    payload_hash = razorpay_events.compute_payload_hash(body_bytes)

    try:
        raw_payload = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON payload")

    # --- Signature verification ---
    # Strategy: per-tenant secret takes precedence; global secret is
    # NOT a fallback when a tenant mapping exists.
    account_id = raw_payload.get("account_id", "")
    tenant_id = None
    webhook_secret = None
    mapping_exists = False

    if account_id:
        tenant_id = await _resolve_tenant_from_webhook(account_id)
        if tenant_id:
            mapping_exists = True
            # Look up per-tenant webhook_secret from the mapping
            db = await get_db()
            try:
                cursor = await db.execute(
                    "SELECT webhook_secret FROM razorpay_account_mappings WHERE account_id = ?",
                    (account_id,),
                )
                row = await cursor.fetchone()
                if row and row["webhook_secret"]:
                    webhook_secret = row["webhook_secret"]
            finally:
                await db.close()

    if mapping_exists and not webhook_secret:
        # Tenant mapping exists but has no per-tenant secret configured.
        # Reject rather than fall back to global — this is a misconfiguration
        # that could allow cross-tenant signature bypass.
        logger.warning(
            "Webhook for account %s has tenant mapping but no webhook_secret configured. "
            "Rejecting to prevent security bypass.", account_id,
        )
        raise HTTPException(
            503,
            f"Tenant mapping for account '{account_id}' exists but has no webhook_secret. "
            f"Set a per-tenant webhook_secret via POST /api/razorpay/accounts.",
        )

    if not webhook_secret:
        # No mapping at all — fall back to global secret
        webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")

    if not webhook_secret:
        raise HTTPException(503, "Razorpay webhook secret not configured.")

    signature = request.headers.get("X-Razorpay-Signature", "")
    if not signature:
        raise HTTPException(401, "Missing webhook signature")

    if not razorpay_client.verify_webhook_signature(body_bytes, signature, webhook_secret):
        raise HTTPException(401, "Invalid signature")

    if "event" not in raw_payload:
        raise HTTPException(400, "Missing 'event' field in payload")

    # --- Tenant resolution ---
    if not account_id:
        raise HTTPException(400, "Webhook payload missing 'account_id' — cannot identify merchant.")

    if not tenant_id:
        # Unknown merchant — reject, do NOT fall back to default
        logger.warning("Webhook from unknown Razorpay account %s — rejecting.", account_id)
        await log_audit("unknown", "webhook.rejected_unknown_merchant", "razorpay_account", account_id,
                        details={"account_id": account_id, "reason": "No tenant mapping for this account."})
        raise HTTPException(404, f"No tenant mapping for Razorpay account '{account_id}'. Register the mapping first.")

    # --- Idempotency check (scoped to resolved tenant) ---
    existing = await razorpay_events.get_event_by_payload_hash(payload_hash, tenant_id=tenant_id)
    if existing:
        return {"status": "already_received", "event_id": existing["event_id"],
                "event_type": existing["event_type"], "tenant_id": tenant_id,
                "message": "Event already processed (idempotent)."}

    # --- Store event under the resolved tenant ---
    event = await razorpay_events.store_event(
        raw_payload, source="live_webhook", verification_status="verified",
        payload_hash=payload_hash, tenant_id=tenant_id,
    )

    await log_audit(tenant_id, "webhook.received", "razorpay_event", event["event_id"],
                    details={"account_id": account_id, "event_type": event["event_type"]})

    return {"status": "received", "event_id": event["event_id"], "event_type": event["event_type"],
            "tenant_id": tenant_id, "verification": "verified",
            "message": "Event stored as financial evidence."}


class SimulateRequest(BaseModel):
    event_type: str = "payment.captured"
    amount: int = 100000
    currency: str = "INR"
    order_id: str = ""
    payment_id: str = ""
    status: str = "captured"


@router.post("/webhooks/razorpay/simulate")
async def simulate_webhook(req: SimulateRequest, user: CurrentUser = Depends(get_current_user)):
    """Local webhook simulator for development. Events are tagged as LOCAL SIMULATOR."""
    now_ts = int(time.time())
    payment_id = req.payment_id or f"pay_{int(time.time() * 1000)}"
    order_id = req.order_id or f"order_{int(time.time() * 1000)}"

    payload = {
        "id": f"evt_{int(time.time() * 1000)}",
        "entity": "event",
        "account_id": "acc_DEMO",
        "event": req.event_type,
        "contains": ["payment"],
        "created_at": now_ts,
        "payload": {
            "payment": {"entity": {
                "id": payment_id, "entity": "payment", "amount": req.amount,
                "currency": req.currency, "status": req.status, "order_id": order_id,
                "method": "upi", "amount_refunded": 0, "captured": req.status == "captured",
                "created_at": now_ts,
            }},
            "order": {"entity": {
                "id": order_id, "entity": "order", "amount": req.amount,
                "currency": req.currency, "status": "paid", "created_at": now_ts,
            }},
        },
    }

    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_hash = razorpay_events.compute_payload_hash(payload_bytes)

    event = await razorpay_events.store_event(
        payload, source="local_simulator", verification_status="unverified",
        payload_hash=payload_hash, tenant_id=user.tenant_id,
    )

    await log_audit(user.tenant_id, "razorpay.simulate", "razorpay_event", event["event_id"],
                    user_id=user.user_id, details={"event_type": req.event_type, "amount": req.amount})

    return {"status": "simulated", "event_id": event["event_id"], "event_type": event["event_type"],
            "payment_id": payment_id, "order_id": order_id, "amount": req.amount,
            "source": "local_simulator", "note": "LOCAL WEBHOOK SIMULATOR — not a live Razorpay webhook."}


@router.get("/razorpay/events")
async def list_events(user: CurrentUser = Depends(get_current_user)):
    """List all stored Razorpay events for this tenant."""
    events = await razorpay_events.get_all_events(tenant_id=user.tenant_id)
    return {"events": [_serialize_event(e) for e in events], "total": len(events)}


@router.get("/razorpay/events/{event_id}")
async def get_event(event_id: str, user: CurrentUser = Depends(get_current_user)):
    """Get a single Razorpay event."""
    event = await razorpay_events.get_event_by_id(event_id, tenant_id=user.tenant_id)
    if not event:
        raise HTTPException(404, f"Event {event_id} not found")
    return _serialize_event(event)


@router.get("/razorpay/connection")
async def connection_status(user: CurrentUser = Depends(get_current_user)):
    return razorpay_client.get_connection_info()


@router.get("/razorpay/status")
async def integration_status(user: CurrentUser = Depends(get_current_user)):
    return razorpay_client.get_status()


@router.post("/razorpay/events/{event_id}/process")
async def process_event(event_id: str, user: CurrentUser = Depends(get_current_user)):
    """Process a Razorpay event into EntitlementLedger evidence and decision."""
    from razorpay_adapter import razorpay_event_to_evidence, process_razorpay_event_to_decision

    db = await get_db()
    try:
        event = await razorpay_events.get_event_by_id(event_id, tenant_id=user.tenant_id, db=db)
        if not event:
            raise HTTPException(404, f"Event {event_id} not found")

        source = event.get("source", "unknown")
        verification = event.get("verification_status", "unverified")

        if source == "live_webhook" and verification != "verified":
            raise HTTPException(403, "Cannot process unverified live webhook event.")

        if event.get("linked_decision_id"):
            raise HTTPException(409, f"Event already processed into decision {event['linked_decision_id']}")


        evidence_record = razorpay_event_to_evidence(event)
        evidence_record["tenant_id"] = user.tenant_id

        # Insert evidence idempotently (ON CONFLICT DO NOTHING)
        import hashlib as _hl
        ev_hash = _hl.sha256(evidence_record["raw_content"].encode()).hexdigest()
        await db.execute(
            "INSERT INTO evidence (evidence_id, tenant_id, source_type, raw_content, extracted_facts, "
            "linked_decision_ids, content_hash, version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?) ON CONFLICT DO NOTHING",
            (evidence_record["evidence_id"], user.tenant_id, evidence_record["source_type"],
             evidence_record["raw_content"], json.dumps(evidence_record.get("extracted_facts", [])),
             "[]", ev_hash, datetime.now(timezone.utc).isoformat()),
        )

        # Get prev hash — the TRUE chain tail is the decision whose hash is
        # not referenced as any other decision's prev_decision_hash
        # (created_at ordering ties are unreliable).
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
            (user.tenant_id,),
        )
        prev_row = await cursor.fetchone()
        prev_hash = prev_row["decision_hash"] if prev_row else "genesis"

        # Get policies
        cursor_p = await db.execute("SELECT * FROM policies")
        policy_rows = await cursor_p.fetchall()
        policies = [dict(p) for p in policy_rows]

        decision = process_razorpay_event_to_decision(
            event=event, evidence_record=evidence_record,
            policies=policies, prev_decision_hash=prev_hash,
        )
        decision["tenant_id"] = user.tenant_id

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

        # Link evidence
        await db.execute(
            "UPDATE evidence SET linked_decision_ids = ? WHERE evidence_id = ? AND tenant_id = ?",
            (json.dumps([decision["decision_id"]]), evidence_record["evidence_id"], user.tenant_id),
        )

        await razorpay_events.link_event_to_decision(event_id, decision["decision_id"], tenant_id=user.tenant_id, db=db)
        await db.commit()

        await log_audit(user.tenant_id, "razorpay.processed", "razorpay_event", event_id,
                        user_id=user.user_id,
                        details={"decision_id": decision["decision_id"],
                                 "evidence_id": evidence_record["evidence_id"]})

        return {
            "status": "processed", "event_id": event_id, "event_type": event["event_type"],
            "source": source, "verification_status": verification,
            "evidence_id": evidence_record["evidence_id"],
            "extracted_facts_count": len(evidence_record.get("extracted_facts", [])),
            "decision_id": decision["decision_id"], "decision_status": decision["status"],
            "gross_amount": decision["gross_amount"], "final_amount": decision["final_amount"],
            "decision_hash": decision["decision_hash"], "prev_decision_hash": decision["prev_decision_hash"],
        }
    finally:
        await db.close()


class RazorpaySyncResponse(BaseModel):
    status: str
    sync_type: str
    records_synced: int
    records_failed: int
    duration_ms: int
    errors: list[str] = []


@router.post("/razorpay/sync/{sync_type}", response_model=RazorpaySyncResponse)
async def sync_razorpay_data(
    sync_type: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Sync Razorpay data (orders, payments, settlements) into local database.

    Requires RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to be configured.
    Only users with admin role can trigger sync.
    """
    import time as _time
    if user.role not in ("admin", "manager"):
        raise HTTPException(403, "Only admin/manager roles can trigger sync")

    if not razorpay_client.is_configured():
        raise HTTPException(503, "Razorpay credentials not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET.")

    valid_types = {"orders", "payments", "settlements"}
    if sync_type not in valid_types:
        raise HTTPException(400, f"sync_type must be one of: {', '.join(valid_types)}")

    start = _time.time()
    synced = 0
    failed = 0
    errors = []

    db = await get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()

        # Record sync start
        await db.execute(
            "INSERT INTO razorpay_sync_metadata (tenant_id, sync_type, status, started_at) "
            "VALUES (?, ?, 'running', ?)",
            (user.tenant_id, sync_type, now),
        )
        await db.commit()

        try:
            if sync_type == "orders":
                data = razorpay_client.fetch_orders(count=MAX_SYNC_RECORDS)
                items = data.get("items", [])
                for item in items:
                    try:
                        await db.execute(
                            "INSERT INTO razorpay_orders (order_id, tenant_id, entity_id, amount, currency, status, receipt, notes, raw_payload, first_seen_at, last_synced_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(order_id) DO UPDATE SET amount=excluded.amount, status=excluded.status, raw_payload=excluded.raw_payload, last_synced_at=excluded.last_synced_at",
                            (
                                item["id"], user.tenant_id, item.get("receipt"),
                                item.get("amount", 0), item.get("currency", "INR"),
                                item.get("status", "created"), item.get("receipt", ""),
                                json.dumps(item.get("notes", {})),
                                json.dumps(item), now, now,
                            ),
                        )
                        synced += 1
                    except Exception as e:
                        failed += 1
                        errors.append(f"Order {item.get('id', '?')}: {e}")

            elif sync_type == "payments":
                data = razorpay_client.fetch_payments(count=MAX_SYNC_RECORDS)
                items = data.get("items", [])
                for item in items:
                    try:
                        await db.execute(
                            "INSERT INTO razorpay_payments (payment_id, tenant_id, order_id, entity_id, amount, currency, status, method, captured, amount_refunded, raw_payload, first_seen_at, last_synced_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(payment_id) DO UPDATE SET amount=excluded.amount, status=excluded.status, captured=excluded.captured, amount_refunded=excluded.amount_refunded, raw_payload=excluded.raw_payload, last_synced_at=excluded.last_synced_at",
                            (
                                item["id"], user.tenant_id,
                                item.get("order_id", ""), item.get("entity", ""),
                                item.get("amount", 0), item.get("currency", "INR"),
                                item.get("status", "created"), item.get("method", ""),
                                bool(item.get("captured")),
                                item.get("amount_refunded", 0),
                                json.dumps(item), now, now,
                            ),
                        )
                        synced += 1
                    except Exception as e:
                        failed += 1
                        errors.append(f"Payment {item.get('id', '?')}: {e}")

            elif sync_type == "settlements":
                data = razorpay_client.fetch_settlements(count=MAX_SYNC_RECORDS)
                items = data.get("items", [])
                for item in items:
                    try:
                        await db.execute(
                            "INSERT INTO razorpay_settlements (settlement_id, tenant_id, amount, currency, status, raw_payload, first_seen_at, last_synced_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(settlement_id) DO UPDATE SET amount=excluded.amount, status=excluded.status, raw_payload=excluded.raw_payload, last_synced_at=excluded.last_synced_at",
                            (
                                item["id"], user.tenant_id,
                                item.get("amount", 0), item.get("currency", "INR"),
                                item.get("status", "pending"),
                                json.dumps(item), now, now,
                            ),
                        )
                        synced += 1
                        # Tier 3/4: persist deterministic settlement recon so
                        # the reconciliation engine can link this settlement
                        # to its payment(s) and derive fee/tax evidence —
                        # even when the settlement payload itself carries no
                        # payment_id.  Recon unavailability (e.g. a pending
                        # settlement) is tolerated: the settlement then
                        # surfaces as an auditable UNLINKED case rather than
                        # being guessed onto a payment.
                        try:
                            recon = razorpay_client.fetch_settlement_reconciliation(item["id"])
                        except razorpay_client.RazorpayAPIError as e:
                            errors.append(
                                f"Settlement {item.get('id', '?')}: recon unavailable "
                                f"({e.category}) — will remain unlinked until recon is available")
                            continue
                        for rrow in _recon_rows_from_payload(item["id"], recon):
                            try:
                                await db.execute(
                                    "INSERT INTO razorpay_settlement_recon "
                                    "(recon_id, tenant_id, settlement_id, payment_id, order_id, "
                                    " amount, fee, tax, recon_type, recorded_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                                    "ON CONFLICT(recon_id) DO UPDATE SET "
                                    "payment_id=excluded.payment_id, order_id=excluded.order_id, "
                                    "amount=excluded.amount, fee=excluded.fee, tax=excluded.tax, "
                                    "recon_type=excluded.recon_type, recorded_at=excluded.recorded_at",
                                    (
                                        rrow["recon_id"], user.tenant_id,
                                        rrow["settlement_id"], rrow["payment_id"],
                                        rrow["order_id"], rrow["amount"], rrow["fee"],
                                        rrow["tax"], rrow["recon_type"], now,
                                    ),
                                )
                            except Exception as e:
                                errors.append(
                                    f"Settlement {item.get('id', '?')} recon row: {e}")
                    except Exception as e:
                        failed += 1
                        errors.append(f"Settlement {item.get('id', '?')}: {e}")

            # Update sync metadata
            duration_ms = int((_time.time() - start) * 1000)
            await db.execute(
                "UPDATE razorpay_sync_metadata SET status='completed', records_synced=?, records_failed=?, completed_at=? "
                "WHERE tenant_id=? AND sync_type=? AND status='running'",
                (synced, failed, now, user.tenant_id, sync_type),
            )
            await db.commit()

        except razorpay_client.RazorpayAPIError as e:
            # Controlled upstream failure — record it in sync metadata and
            # map to a precise HTTP status.  Never a generic 500, never a
            # leaked secret.
            duration_ms = int((_time.time() - start) * 1000)
            await db.execute(
                "UPDATE razorpay_sync_metadata SET status='failed', error_message=?, records_synced=?, records_failed=?, completed_at=? "
                "WHERE tenant_id=? AND sync_type=? AND status='running'",
                (f"{e.category}: {e.diagnostic}", synced, failed, now, user.tenant_id, sync_type),
            )
            await db.commit()
            logger.warning("Razorpay sync %s failed: %s", sync_type, e.category)
            await log_audit(user.tenant_id, "razorpay.sync.failed", sync_type, "sync",
                            user_id=user.user_id,
                            details={"sync_type": sync_type, "category": e.category,
                                     "status_code": e.status_code})
            if e.category == "auth":
                raise HTTPException(502, "Razorpay API authentication failed (invalid or unauthorized credentials)")
            if e.category == "rate_limited":
                raise HTTPException(429, "Razorpay API rate limit exceeded — retry later")
            if e.category == "network":
                raise HTTPException(503, "Razorpay API unreachable — check connectivity and retry")
            raise HTTPException(502, "Razorpay API upstream error — retry later")

        except Exception as e:
            duration_ms = int((_time.time() - start) * 1000)
            await db.execute(
                "UPDATE razorpay_sync_metadata SET status='failed', error_message=?, records_synced=?, records_failed=?, completed_at=? "
                "WHERE tenant_id=? AND sync_type=? AND status='running'",
                (str(e), synced, failed, now, user.tenant_id, sync_type),
            )
            await db.commit()
            raise

        await log_audit(user.tenant_id, "razorpay.sync", sync_type, "sync",
                        user_id=user.user_id,
                        details={"sync_type": sync_type, "synced": synced, "failed": failed})

        return RazorpaySyncResponse(
            status="completed" if failed == 0 else "completed_with_errors",
            sync_type=sync_type,
            records_synced=synced,
            records_failed=failed,
            duration_ms=duration_ms,
            errors=errors[:20],  # Cap error list
        )
    finally:
        await db.close()


@router.get("/razorpay/sync/history")
async def sync_history(
    limit: int = 10,
    user: CurrentUser = Depends(get_current_user),
):
    """Return recent sync history for this tenant."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM razorpay_sync_metadata WHERE tenant_id = ? ORDER BY started_at DESC LIMIT ?",
            (user.tenant_id, limit),
        )
        rows = await cursor.fetchall()
        return {"syncs": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.get("/razorpay/synced/{data_type}")
async def get_synced_data(
    data_type: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Return synced Razorpay data (orders, payments, settlements)."""
    valid_types = {"orders": "razorpay_orders", "payments": "razorpay_payments", "settlements": "razorpay_settlements"}
    if data_type not in valid_types:
        raise HTTPException(400, f"data_type must be one of: {', '.join(valid_types)}")

    table = valid_types[data_type]
    db = await get_db()
    try:
        cursor = await db.execute(
            f"SELECT * FROM {table} WHERE tenant_id = ? ORDER BY last_synced_at DESC LIMIT 100",
            (user.tenant_id,),
        )
        rows = await cursor.fetchall()
        return {"count": len(rows), "items": [dict(r) for r in rows]}
    finally:
        await db.close()


def _serialize_event(event) -> dict:
    event_type = event["event_type"]
    # Canonical classification from the single event registry.
    classification = razorpay_registry.classify_event(event_type)
    return {
        "event_id": event["event_id"], "event_type": event_type,
        "source": event.get("source", "unknown"),
        "verification_status": event.get("verification_status", "unverified"),
        "razorpay_entity_type": event.get("razorpay_entity_type", "unknown"),
        "razorpay_entity_id": event.get("razorpay_entity_id", ""),
        "payment_id": event.get("payment_id", ""),
        "order_id": event.get("order_id", ""),
        "amount": event.get("amount"),
        "currency": event.get("currency", "INR"),
        "status": event.get("status", "unknown"),
        "event_timestamp": event.get("event_timestamp"),
        "received_at": event.get("received_at", ""),
        "extracted_facts": _parse_facts(event.get("extracted_facts", "[]")),
        "linked_decision_id": event.get("linked_decision_id"),
        "event_family": classification["family"],
        "known_event": classification["known"],
        "financial_relevance": classification["financial_relevance"],
        "affects_reconciliation": classification["affects_reconciliation"],
        "context_risk_only": classification["context_risk_only"],
    }


def _parse_facts(val) -> list:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return []
    return val if isinstance(val, list) else []
