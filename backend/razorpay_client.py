"""Minimal Razorpay REST API client for test mode.

Only supports the minimum operations needed for the MVP:
- Fetch payment by ID
- Fetch order by ID
- Fetch settlements
- Webhook signature verification (raw bytes, constant-time)

Uses httpx directly — no CLI, no MCP, no n8n dependency.
Credentials come from environment variables and are never logged.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.razorpay.com/v1"


def _get_credentials() -> tuple[Optional[str], Optional[str]]:
    """Return (key_id, key_secret) from env. Either or both may be None."""
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    return key_id, key_secret


def is_configured() -> bool:
    """Return True if Razorpay credentials are present."""
    key_id, key_secret = _get_credentials()
    return bool(key_id and key_secret)


def get_connection_info() -> dict:
    """Return connection status (never exposes secrets)."""
    key_id, key_secret = _get_credentials()
    webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    return {
        "configured": bool(key_id and key_secret),
        "key_id_present": bool(key_id),
        "key_id_preview": f"{key_id[:8]}..." if key_id else None,
        "webhook_secret_present": bool(webhook_secret),
        "mode": "test" if key_id and key_id.startswith("rzp_test_") else ("live" if key_id else "none"),
    }


def get_status() -> dict:
    """Return integration status for the /api/razorpay/status endpoint.

    Reports live vs demo mode. Never exposes secret values.
    """
    key_id, key_secret = _get_credentials()
    webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    configured = bool(key_id and key_secret)
    return {
        "configured": configured,
        "mode": "live" if configured else "demo",
        "webhook_configured": bool(webhook_secret),
        "key_id_preview": f"{key_id[:8]}..." if key_id else None,
    }


def _request(method: str, path: str, **kwargs) -> dict:
    """Make an authenticated Razorpay API request."""
    key_id, key_secret = _get_credentials()
    if not key_id or not key_secret:
        raise EnvironmentError(
            "Razorpay credentials not configured. "
            "Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET."
        )

    url = f"{BASE_URL}{path}"
    logger.info("Razorpay %s %s", method.upper(), path)

    with httpx.Client(timeout=15.0) as client:
        resp = client.request(
            method,
            url,
            auth=(key_id, key_secret),
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json()


def fetch_payment(payment_id: str) -> dict:
    """Fetch a payment by ID."""
    return _request("GET", f"/payments/{payment_id}")


def fetch_order(order_id: str) -> dict:
    """Fetch an order by ID."""
    return _request("GET", f"/orders/{order_id}")


def fetch_settlements(count: int = 10, skip: int = 0) -> dict:
    """Fetch settlements list.

    Razorpay API: GET /v1/settlements
    Returns: {"count": int, "entity": "list", "items": [...]}
    """
    return _request("GET", "/settlements", params={"count": count, "skip": skip})


def fetch_payments(count: int = 10, skip: int = 0, from_ts: int = None, to_ts: int = None) -> dict:
    """Fetch payments list with optional time range filter.

    Razorpay API: GET /v1/payments
    Returns: {"count": int, "entity": "list", "items": [...]}
    """
    params = {"count": count, "skip": skip}
    if from_ts:
        params["from"] = from_ts
    if to_ts:
        params["to"] = to_ts
    return _request("GET", "/payments", params=params)


def fetch_orders(count: int = 10, skip: int = 0, from_ts: int = None, to_ts: int = None) -> dict:
    """Fetch orders list with optional time range filter.

    Razorpay API: GET /v1/orders
    Returns: {"count": int, "entity": "list", "items": [...]}
    """
    params = {"count": count, "skip": skip}
    if from_ts:
        params["from"] = from_ts
    if to_ts:
        params["to"] = to_ts
    return _request("GET", "/orders", params=params)


def fetch_payments_for_order(order_id: str) -> dict:
    """Fetch all payments for a specific order.

    Razorpay API: GET /v1/orders/{order_id}/payments
    """
    return _request("GET", f"/orders/{order_id}/payments")


def fetch_settlement_reconciliation(settlement_id: str) -> dict:
    """Fetch settlement reconciliation data.

    Razorpay API: GET /v1/settlements/{settlement_id}/recon
    """
    return _request("GET", f"/settlements/{settlement_id}/recon")


def test_connection() -> dict:
    """Test API connectivity by fetching account balance.

    Returns: {"ok": bool, "balance": int|None, "error": str|None}
    """
    try:
        result = _request("GET", "/payments/account")
        return {"ok": True, "balance": None, "error": None, "account": result.get("id", "")}
    except EnvironmentError as e:
        return {"ok": False, "balance": None, "error": str(e)}
    except Exception as e:
        return {"ok": False, "balance": None, "error": str(e)}


# ---------------------------------------------------------------------------
# Webhook signature verification — RAW BYTES, CONSTANT-TIME
# ---------------------------------------------------------------------------

def verify_webhook_signature(
    body: bytes,
    signature: str,
    secret: str,
) -> bool:
    """Verify Razorpay webhook HMAC-SHA256 signature.

    Razorpay signs the raw request body bytes — not a re-serialized JSON
    object. This function computes the HMAC over the exact raw bytes
    provided, and uses hmac.compare_digest for constant-time comparison
    to prevent timing attacks.

    Args:
        body: The raw request body as bytes (from request.body()).
        signature: The X-Razorpay-Signature header value.
        secret: The RAZORPAY_WEBHOOK_SECRET.

    Returns:
        True if the signature is valid.
    """
    if not secret or not signature:
        return False
    try:
        expected = hmac.new(
            secret.encode("utf-8"),
            body,  # raw bytes — NOT body.decode().encode()
            hashlib.sha256,
        ).hexdigest()
        # Constant-time comparison — never use == for signature checks
        return hmac.compare_digest(expected, signature)
    except Exception as e:
        logger.warning("Webhook signature verification error: %s", e)
        return False
