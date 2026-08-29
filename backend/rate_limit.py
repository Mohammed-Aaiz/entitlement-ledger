"""Production-safe rate limiting for EntitlementLedger.

Uses an in-memory sliding window counter — no external dependency needed.
Returns HTTP 429 with Retry-After header when limit is exceeded.

Rate limits are per-IP and reset automatically.
"""
from __future__ import annotations

import os
import time
import logging
from collections import defaultdict
from typing import Optional

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)

ENV = os.environ.get("ENV", "development")
TESTING = os.environ.get("TESTING", "false").lower() in ("true", "1", "yes")


class _SlidingWindowCounter:
    """Per-IP sliding window rate limiter."""

    def __init__(self, window_seconds: int, max_requests: int):
        self.window = window_seconds
        self.max_requests = max_requests
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> tuple[bool, Optional[int]]:
        """Check if request is allowed. Returns (allowed, retry_after_seconds)."""
        now = time.time()
        cutoff = now - self.window

        # Prune old entries
        hits = self._hits[key]
        self._hits[key] = [t for t in hits if t > cutoff]

        if len(self._hits[key]) >= self.max_requests:
            oldest = self._hits[key][0]
            retry_after = int(oldest + self.window - now) + 1
            return False, max(retry_after, 1)

        self._hits[key].append(now)
        return True, None


# Rate limit configurations
_LOGIN_LIMITER = _SlidingWindowCounter(window_seconds=60, max_requests=10)
_REGISTER_LIMITER = _SlidingWindowCounter(window_seconds=300, max_requests=5)
_WEBHOOK_LIMITER = _SlidingWindowCounter(window_seconds=60, max_requests=100)


def reset_all_limiters():
    """Reset all rate limiters (for testing)."""
    _LOGIN_LIMITER._hits.clear()
    _REGISTER_LIMITER._hits.clear()
    _WEBHOOK_LIMITER._hits.clear()


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind a proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global rate limiting middleware.

    Applies different limits based on the request path:
    - /api/auth/login: 10/min per IP
    - /api/auth/register: 5/5min per IP
    - /api/webhooks: 100/min per IP
    - Other /api/*: no limit (can be added)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Check os.environ each time (not cached) for test compatibility
        if os.environ.get("TESTING", "false").lower() in ("true", "1", "yes"):
            return await call_next(request)

        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        client_ip = _get_client_ip(request)
        path = request.url.path

        # Login rate limit
        if path == "/api/auth/login" and request.method == "POST":
            allowed, retry_after = _LOGIN_LIMITER.is_allowed(f"login:{client_ip}")
            if not allowed:
                logger.warning("Rate limit exceeded for login from %s", client_ip)
                return Response(
                    content='{"detail": "Too many login attempts. Please try again later."}',
                    status_code=429,
                    headers={
                        "Content-Type": "application/json",
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": "10",
                        "X-RateLimit-Remaining": "0",
                    },
                )

        # Registration rate limit
        if path == "/api/auth/register" and request.method == "POST":
            allowed, retry_after = _REGISTER_LIMITER.is_allowed(f"register:{client_ip}")
            if not allowed:
                logger.warning("Rate limit exceeded for registration from %s", client_ip)
                return Response(
                    content='{"detail": "Too many registration attempts. Please try again later."}',
                    status_code=429,
                    headers={
                        "Content-Type": "application/json",
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": "5",
                        "X-RateLimit-Remaining": "0",
                    },
                )

        # Webhook rate limit
        if path == "/api/webhooks/razorpay" and request.method == "POST":
            allowed, retry_after = _WEBHOOK_LIMITER.is_allowed(f"webhook:{client_ip}")
            if not allowed:
                logger.warning("Rate limit exceeded for webhooks from %s", client_ip)
                return Response(
                    content='{"detail": "Too many webhook events."}',
                    status_code=429,
                    headers={
                        "Content-Type": "application/json",
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": "100",
                        "X-RateLimit-Remaining": "0",
                    },
                )

        return await call_next(request)
