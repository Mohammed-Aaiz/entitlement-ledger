"""Tests for rate limiting middleware."""
import os
import pytest


class TestSlidingWindowCounter:
    """Unit tests for the sliding window counter."""

    def test_allows_within_limit(self):
        from rate_limit import _SlidingWindowCounter
        counter = _SlidingWindowCounter(window_seconds=60, max_requests=5)
        for _ in range(5):
            allowed, retry_after = counter.is_allowed("ip1")
            assert allowed is True
            assert retry_after is None

    def test_blocks_at_limit(self):
        from rate_limit import _SlidingWindowCounter
        counter = _SlidingWindowCounter(window_seconds=60, max_requests=3)
        for _ in range(3):
            allowed, _ = counter.is_allowed("ip1")
            assert allowed is True

        allowed, retry_after = counter.is_allowed("ip1")
        assert allowed is False
        assert retry_after is not None
        assert retry_after > 0

    def test_different_ips_independent(self):
        from rate_limit import _SlidingWindowCounter
        counter = _SlidingWindowCounter(window_seconds=60, max_requests=2)
        counter.is_allowed("ip1")
        counter.is_allowed("ip1")

        # ip2 is separate
        allowed, _ = counter.is_allowed("ip2")
        assert allowed is True

    def test_entries_expire(self):
        from rate_limit import _SlidingWindowCounter
        counter = _SlidingWindowCounter(window_seconds=1, max_requests=1)
        counter.is_allowed("ip1")
        allowed, _ = counter.is_allowed("ip1")
        assert allowed is False

        # After window expires, should be allowed again
        import time
        time.sleep(1.1)
        allowed, _ = counter.is_allowed("ip1")
        assert allowed is True


class TestRateLimitMiddleware:
    """Integration tests for rate limiting on actual endpoints."""

    def test_login_returns_429_when_limit_exceeded(self, client):
        """Verify the middleware returns 429 with Retry-After header."""
        # Temporarily disable TESTING for this test
        old_testing = os.environ.get("TESTING")
        os.environ.pop("TESTING", None)
        try:
            from rate_limit import reset_all_limiters
            reset_all_limiters()

            # Exceed the login rate limit (10 per minute)
            for _ in range(10):
                client.post("/api/auth/login", json={
                    "email": "nonexistent@test.com", "password": "wrongpassword"
                })

            resp = client.post("/api/auth/login", json={
                "email": "nonexistent@test.com", "password": "wrongpassword"
            })
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers

            reset_all_limiters()
        finally:
            if old_testing is not None:
                os.environ["TESTING"] = old_testing

    def test_register_returns_429_when_limit_exceeded(self, client):
        """Verify the middleware returns 429 with Retry-After header."""
        old_testing = os.environ.get("TESTING")
        os.environ.pop("TESTING", None)
        try:
            from rate_limit import reset_all_limiters
            reset_all_limiters()

            # Exceed the registration rate limit (5 per 5 minutes)
            for i in range(5):
                client.post("/api/auth/register", json={
                    "email": f"ratelimit_test_{i}@example.com",
                    "password": "securepass123",
                    "display_name": f"Rate Limit Test {i}",
                })

            resp = client.post("/api/auth/register", json={
                "email": "ratelimit_test_extra@example.com",
                "password": "securepass123",
                "display_name": "Extra User",
            })
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers

            reset_all_limiters()
        finally:
            if old_testing is not None:
                os.environ["TESTING"] = old_testing

    def test_skips_rate_limiting_in_test_mode(self, client):
        """When TESTING=true, rate limiting is disabled."""
        from rate_limit import reset_all_limiters
        reset_all_limiters()

        # Should not be rate limited even with many requests
        for _ in range(15):
            resp = client.post("/api/auth/login", json={
                "email": "nonexistent@test.com", "password": "wrongpassword"
            })
            # Should get 401 (wrong creds), not 429
            assert resp.status_code == 401
