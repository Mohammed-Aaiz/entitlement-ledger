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


class RazorpayAPIError(Exception):
    """Controlled upstream Razorpay API failure.

    Raised instead of letting httpx exceptions surface as unhandled 500s.
    Carries a stable category + the upstream status code so routes can map
    it to the correct HTTP response.  Never contains credentials; the
    diagnostic is a sanitized status-line summary only.
    """

    def __init__(self, category: str, status_code: int = 0, diagnostic: str = ""):
        self.category = category  # auth | rate_limited | unavailable | bad_gateway | network
        self.status_code = status_code
        self.diagnostic = diagnostic
        super().__init__(f"Razorpay API {category} (HTTP {status_code or 'n/a'}): {diagnostic}")


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


def _detect_mode(key_id: Optional[str]) -> str:
    """Detect the Razorpay mode from the key prefix.

    A configured rzp_test_ key is TEST mode; rzp_live_ (or a non-test
    key id) is LIVE mode; no key is 'none'.  Mode is never inferred from
    mere presence of credentials.
    """
    if not key_id:
        return "none"
    if key_id.startswith("rzp_test_"):
        return "test"
    return "live"


def get_status() -> dict:
    """Return integration status for the /api/razorpay/status endpoint.

    Reports test/live/none mode derived from the key prefix — test
    credentials are NEVER reported as LIVE MODE.  Never exposes secrets.
    """
    key_id, key_secret = _get_credentials()
    webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    configured = bool(key_id and key_secret)
    # mode: 'test' for rzp_test_ keys (NEVER live), 'live' for live keys,
    # 'demo' when not configured (backwards-compatible with the UI).
    mode = _detect_mode(key_id) if configured else "demo"
    return {
        "configured": configured,
        "mode": mode,
        "test_mode": mode == "test",
        "webhook_configured": bool(webhook_secret),
        "key_id_preview": f"{key_id[:8]}..." if key_id else None,
    }


def _request(method: str, path: str, **kwargs) -> dict:
    """Make an authenticated Razorpay API request.

    Upstream failures are mapped to controlled RazorpayAPIError instances
    (auth / rate_limited / unavailable / bad_gateway / network) instead of
    leaking as unhandled httpx exceptions.  Never includes secrets in the
    diagnostic.
    """
    key_id, key_secret = _get_credentials()
    if not key_id or not key_secret:
        raise RazorpayAPIError(
            "auth", 0, "Razorpay credentials not configured (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET)"
        )

    url = f"{BASE_URL}{path}"
    logger.info("Razorpay %s %s", method.upper(), path)

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.request(
                method,
                url,
                auth=(key_id, key_secret),
                **kwargs,
            )
    except httpx.TimeoutException:
        raise RazorpayAPIError("network", 0, "Razorpay request timed out")
    except httpx.RequestError as e:
        raise RazorpayAPIError("network", 0, f"Razorpay connection error: {type(e).__name__}")

    if resp.status_code >= 400:
        _raise_upstream_error(resp.status_code)
    return resp.json()


def _raise_upstream_error(status_code: int) -> None:
    """Map a Razorpay upstream HTTP status to a controlled error."""
    if status_code in (401, 403):
        raise RazorpayAPIError(
            "auth", status_code,
            "Razorpay API authentication failed (invalid or unauthorized credentials)",
        )
    if status_code == 429:
        raise RazorpayAPIError(
            "rate_limited", status_code,
            "Razorpay API rate limit exceeded",
        )
    if status_code >= 500:
        category = "unavailable" if status_code == 503 else "bad_gateway"
        raise RazorpayAPIError(
            category, status_code,
            f"Razorpay API upstream error (HTTP {status_code})",
        )
    raise RazorpayAPIError(
        "bad_gateway", status_code,
        f"Razorpay API unexpected error (HTTP {status_code})",
    )


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

    Returns: {"ok": bool, "balance": int|None, "error": str|None,
    "category": str|None, "status_code": int}
    """
    try:
        result = _request("GET", "/payments/account")
        return {"ok": True, "balance": None, "error": None, "account": result.get("id", ""),
                "category": None, "status_code": 200}
    except RazorpayAPIError as e:
        return {"ok": False, "balance": None, "error": e.diagnostic,
                "category": e.category, "status_code": e.status_code}
    except EnvironmentError as e:
        return {"ok": False, "balance": None, "error": str(e),
                "category": "auth", "status_code": 0}
    except Exception as e:
        return {"ok": False, "balance": None, "error": str(e),
                "category": "bad_gateway", "status_code": 0}


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
