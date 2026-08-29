"""Comprehensive authentication tests.

Tests all auth flows: registration, login, token validation, error cases,
and protected endpoint access control.
"""
import pytest


class TestRegistration:

    def test_register_new_user(self, client):
        """Successful registration returns JWT + user info."""
        resp = client.post("/api/auth/register", json={
            "email": "newuser@company.com",
            "password": "securepass123",
            "display_name": "New User",
            "tenant_name": "acme_corp",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["email"] == "newuser@company.com"
        assert data["role"] == "admin"  # first user in tenant becomes admin
        assert data["tenant_id"] == "acme_corp"

    def test_register_duplicate_email(self, client):
        """Duplicate email returns 409."""
        # Register once
        client.post("/api/auth/register", json={
            "email": "dup@company.com",
            "password": "securepass123",
            "display_name": "First User",
            "tenant_name": "dup_tenant",
        })
        # Register again with same email
        resp = client.post("/api/auth/register", json={
            "email": "dup@company.com",
            "password": "securepass123",
            "display_name": "Second User",
            "tenant_name": "dup_tenant",
        })
        assert resp.status_code == 409
        assert "already registered" in resp.json()["detail"].lower()

    def test_register_short_password(self, client):
        """Password < 8 chars returns 400."""
        resp = client.post("/api/auth/register", json={
            "email": "short@company.com",
            "password": "short",
            "display_name": "Short",
            "tenant_name": "short_tenant",
        })
        assert resp.status_code == 400

    def test_register_missing_fields(self, client):
        """Missing required fields returns 422."""
        resp = client.post("/api/auth/register", json={
            "email": "incomplete@company.com",
            # Missing password
        })
        assert resp.status_code == 422


class TestLogin:

    def test_login_success(self, client):
        """Successful login returns JWT + user info."""
        resp = client.post("/api/auth/login", json={
            "email": "test@demo.ledger",
            "password": "test1234",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["email"] == "test@demo.ledger"
        assert data["role"] == "admin"

    def test_login_wrong_password(self, client):
        """Wrong password returns 401."""
        resp = client.post("/api/auth/login", json={
            "email": "test@demo.ledger",
            "password": "wrongpassword",
        })
        assert resp.status_code == 401
        assert "invalid" in resp.json()["detail"].lower()

    def test_login_nonexistent_user(self, client):
        """Non-existent user returns 401 (same as wrong password — no user enumeration)."""
        resp = client.post("/api/auth/login", json={
            "email": "nobody@nowhere.com",
            "password": "whatever1234",
        })
        assert resp.status_code == 401
        assert "invalid" in resp.json()["detail"].lower()

    def test_login_empty_body(self, client):
        """Empty body returns 422."""
        resp = client.post("/api/auth/login", json={})
        assert resp.status_code == 422


class TestMe:

    def test_me_with_valid_token(self, auth_client):
        """Authenticated /me returns user info."""
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "test@demo.ledger"
        assert data["role"] == "admin"
        assert data["tenant_id"] == "demo"

    def test_me_without_token(self, client):
        """Unauthenticated /me returns 401."""
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401
        assert "authentication required" in resp.json()["detail"].lower()

    def test_me_with_invalid_token(self, client):
        """Invalid token returns 401."""
        resp = client.get("/api/auth/me", headers={"Authorization": "Bearer invalid.jwt.token"})
        assert resp.status_code == 401


class TestProtectedEndpoints:

    def test_stats_requires_auth(self, client):
        """/api/stats without auth returns 401."""
        resp = client.get("/api/stats")
        assert resp.status_code == 401

    def test_stats_with_auth(self, auth_client):
        """/api/stats with auth returns data."""
        resp = auth_client.get("/api/stats")
        assert resp.status_code == 200
        assert "total_decisions" in resp.json()

    def test_decisions_requires_auth(self, client):
        """/api/decisions without auth returns 401."""
        resp = client.get("/api/decisions")
        assert resp.status_code == 401

    def test_analyze_requires_auth(self, client):
        """/api/decisions/analyze without auth returns 401."""
        resp = client.post("/api/decisions/analyze", json={
            "entity_id": "test",
            "gross_amount": 10000,
            "evidence_items": [{"source_type": "order", "raw_content": "{}"}],
        })
        assert resp.status_code == 401

    def test_policies_requires_auth(self, client):
        """/api/policies without auth returns 401."""
        resp = client.get("/api/policies")
        assert resp.status_code == 401

    def test_scenarios_requires_auth(self, client):
        """/api/scenarios without auth returns 401."""
        resp = client.get("/api/scenarios")
        assert resp.status_code == 401

    def test_tenant_isolation(self, client):
        """Different tenants see different data."""
        # Register two users in different tenants
        r1 = client.post("/api/auth/register", json={
            "email": "alice@alpha.com", "password": "securepass123",
            "display_name": "Alice", "tenant_name": "alpha",
        }).json()
        r2 = client.post("/api/auth/register", json={
            "email": "bob@beta.com", "password": "securepass123",
            "display_name": "Bob", "tenant_name": "beta",
        }).json()

        t1 = r1["access_token"]
        t2 = r2["access_token"]

        # Alice sees only alpha's data
        s1 = client.get("/api/stats", headers={"Authorization": f"Bearer {t1}"}).json()
        # Bob sees only beta's data
        s2 = client.get("/api/stats", headers={"Authorization": f"Bearer {t2}"}).json()

        # Both tenants have no data (not demo tenants)
        assert s1["total_decisions"] == 0
        assert s2["total_decisions"] == 0

    def test_second_user_in_tenant_gets_analyst_role(self, client):
        """Second user in a tenant gets 'analyst' role, not 'admin'."""
        client.post("/api/auth/register", json={
            "email": "first@team.com", "password": "securepass123",
            "display_name": "First", "tenant_name": "team",
        })
        r = client.post("/api/auth/register", json={
            "email": "second@team.com", "password": "securepass123",
            "display_name": "Second", "tenant_name": "team",
        }).json()
        assert r["role"] == "analyst"
