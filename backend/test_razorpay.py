"""Tests for Razorpay integration — webhook, simulate, process, status.

All authenticated endpoints use the auth_client fixture.
Webhook endpoints (no auth) are tested separately.
"""
import hashlib
import hmac
import json
import os
import pytest
import time


class TestWebhookSecurity:
    """POST /api/webhooks/razorpay security contracts."""

    def test_no_secret_returns_503(self, client):
        """Without RAZORPAY_WEBHOOK_SECRET, webhooks are rejected with 503."""
        # Ensure no secret is set
        os.environ.pop("RAZORPAY_WEBHOOK_SECRET", None)
        payload = json.dumps({"event": "payment.captured", "payload": {}}).encode()
        resp = client.post("/api/webhooks/razorpay", content=payload,
                          headers={"Content-Type": "application/json"})
        assert resp.status_code == 503

    def test_missing_signature_returns_401(self, client):
        os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test_secret_123"
        try:
            payload = json.dumps({"event": "payment.captured"}).encode()
            resp = client.post("/api/webhooks/razorpay", content=payload,
                              headers={"Content-Type": "application/json"})
            assert resp.status_code == 401
        finally:
            os.environ.pop("RAZORPAY_WEBHOOK_SECRET", None)

    def test_invalid_signature_returns_401(self, client):
        os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test_secret_123"
        try:
            payload = json.dumps({"event": "payment.captured"}).encode()
            resp = client.post("/api/webhooks/razorpay", content=payload,
                              headers={"Content-Type": "application/json",
                                       "X-Razorpay-Signature": "bad_signature"})
            assert resp.status_code == 401
        finally:
            os.environ.pop("RAZORPAY_WEBHOOK_SECRET", None)

    def test_valid_signature_accepted(self, auth_client):
        """Valid signature + known account_id + registered mapping = accepted."""
        os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test_secret_123"
        try:
            # Register the account mapping WITH a per-tenant webhook_secret
            resp = auth_client.post("/api/razorpay/accounts", json={
                "account_id": "acc_TEST_001", "tenant_id": "demo",
                "webhook_secret": "test_secret_123",
            })
            assert resp.status_code == 200

            payload = json.dumps({
                "event": "payment.captured",
                "account_id": "acc_TEST_001",
                "payload": {},
            }).encode()
            expected_sig = hmac.new(b"test_secret_123", payload, hashlib.sha256).hexdigest()
            resp = auth_client.post("/api/webhooks/razorpay", content=payload,
                              headers={"Content-Type": "application/json",
                                       "X-Razorpay-Signature": expected_sig})
            assert resp.status_code == 200
            assert resp.json()["status"] == "received"
            assert resp.json()["tenant_id"] == "demo"
        finally:
            os.environ.pop("RAZORPAY_WEBHOOK_SECRET", None)


class TestWebhookTenantResolution:
    """Webhook tenant resolution from account_id."""

    def test_unknown_merchant_rejected(self, client):
        """Webhook from unregistered Razorpay account is rejected."""
        os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test_secret_123"
        try:
            payload = json.dumps({
                "event": "payment.captured",
                "account_id": "acc_UNKNOWN",
                "payload": {},
            }).encode()
            sig = hmac.new(b"test_secret_123", payload, hashlib.sha256).hexdigest()
            resp = client.post("/api/webhooks/razorpay", content=payload,
                              headers={"Content-Type": "application/json",
                                       "X-Razorpay-Signature": sig})
            assert resp.status_code == 404
            assert "No tenant mapping" in resp.json()["detail"]
        finally:
            os.environ.pop("RAZORPAY_WEBHOOK_SECRET", None)

    def test_missing_account_id_rejected(self, client):
        """Webhook without account_id is rejected."""
        os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test_secret_123"
        try:
            payload = json.dumps({"event": "payment.captured", "payload": {}}).encode()
            sig = hmac.new(b"test_secret_123", payload, hashlib.sha256).hexdigest()
            resp = client.post("/api/webhooks/razorpay", content=payload,
                              headers={"Content-Type": "application/json",
                                       "X-Razorpay-Signature": sig})
            assert resp.status_code == 400
            assert "account_id" in resp.json()["detail"].lower()
        finally:
            os.environ.pop("RAZORPAY_WEBHOOK_SECRET", None)

    def test_account_mapping_requires_admin(self, client):
        """Only admins can register account mappings."""
        resp = client.post("/api/razorpay/accounts", json={
            "account_id": "acc_TEST", "tenant_id": "demo",
        })
        assert resp.status_code in (401, 403)

    def test_account_mapping_works(self, auth_client):
        """Admin can register account mapping."""
        resp = auth_client.post("/api/razorpay/accounts", json={
            "account_id": "acc_TEST_MAP", "tenant_id": "demo",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "mapped"

    def test_webhook_resolves_correct_tenant(self, auth_client):
        """Webhook with known account_id is stored under correct tenant."""
        os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test_secret_123"
        try:
            # Register mapping WITH per-tenant secret
            auth_client.post("/api/razorpay/accounts", json={
                "account_id": "acc_DEMO_001", "tenant_id": "demo",
                "webhook_secret": "test_secret_123",
            })

            payload = json.dumps({
                "event": "payment.captured",
                "account_id": "acc_DEMO_001",
                "payload": {"payment": {"entity": {
                    "id": "pay_test_001", "amount": 50000, "currency": "INR",
                    "status": "captured", "order_id": "order_test_001",
                }}},
            }).encode()
            sig = hmac.new(b"test_secret_123", payload, hashlib.sha256).hexdigest()
            resp = auth_client.post("/api/webhooks/razorpay", content=payload,
                              headers={"Content-Type": "application/json",
                                       "X-Razorpay-Signature": sig})
            assert resp.status_code == 200
            assert resp.json()["tenant_id"] == "demo"
        finally:
            os.environ.pop("RAZORPAY_WEBHOOK_SECRET", None)

    def test_global_secret_not_fallback_for_mapped_tenant(self, auth_client):
        """Mapping exists without per-tenant secret => reject (503).

        The global RAZORPAY_WEBHOOK_SECRET must NOT be used as a fallback
        when a tenant mapping exists. This prevents cross-tenant bypass.
        """
        os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test_secret_123"
        try:
            # Register mapping WITHOUT a per-tenant webhook_secret
            auth_client.post("/api/razorpay/accounts", json={
                "account_id": "acc_NO_SECRET", "tenant_id": "demo",
            })

            payload = json.dumps({
                "event": "payment.captured",
                "account_id": "acc_NO_SECRET",
                "payload": {},
            }).encode()
            sig = hmac.new(b"test_secret_123", payload, hashlib.sha256).hexdigest()
            resp = auth_client.post("/api/webhooks/razorpay", content=payload,
                              headers={"Content-Type": "application/json",
                                       "X-Razorpay-Signature": sig})
            assert resp.status_code == 503
            assert "webhook_secret" in resp.json()["detail"].lower()
        finally:
            os.environ.pop("RAZORPAY_WEBHOOK_SECRET", None)

    def test_per_tenant_secret_required_not_global(self, auth_client):
        """When per-tenant secret is set, global secret must NOT work."""
        os.environ["RAZORPAY_WEBHOOK_SECRET"] = "global_secret"
        try:
            # Register mapping with its own secret
            auth_client.post("/api/razorpay/accounts", json={
                "account_id": "acc_TENANT_A", "tenant_id": "demo",
                "webhook_secret": "tenant_a_secret",
            })

            # Sign with global secret — should fail
            payload = json.dumps({
                "event": "payment.captured",
                "account_id": "acc_TENANT_A",
                "payload": {},
            }).encode()
            bad_sig = hmac.new(b"global_secret", payload, hashlib.sha256).hexdigest()
            resp = auth_client.post("/api/webhooks/razorpay", content=payload,
                              headers={"Content-Type": "application/json",
                                       "X-Razorpay-Signature": bad_sig})
            assert resp.status_code == 401

            # Sign with per-tenant secret — should succeed
            good_sig = hmac.new(b"tenant_a_secret", payload, hashlib.sha256).hexdigest()
            resp2 = auth_client.post("/api/webhooks/razorpay", content=payload,
                              headers={"Content-Type": "application/json",
                                       "X-Razorpay-Signature": good_sig})
            assert resp2.status_code == 200
        finally:
            os.environ.pop("RAZORPAY_WEBHOOK_SECRET", None)


class TestSimulator:
    """POST /api/webhooks/razorpay/simulate"""

    def test_simulate_works_without_credentials(self, auth_client):
        """Simulator always works without Razorpay credentials."""
        os.environ.pop("RAZORPAY_KEY_ID", None)
        os.environ.pop("RAZORPAY_KEY_SECRET", None)
        resp = auth_client.post("/api/webhooks/razorpay/simulate", json={
            "event_type": "payment.captured",
            "amount": 100000,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "simulated"
        assert data["source"] == "local_simulator"

    def test_simulate_requires_auth(self, client):
        """Simulator requires authentication."""
        resp = client.post("/api/webhooks/razorpay/simulate", json={"amount": 100000})
        assert resp.status_code in (401, 403)

    def test_simulate_returns_event_data(self, auth_client):
        resp = auth_client.post("/api/webhooks/razorpay/simulate", json={
            "event_type": "payment.captured",
            "amount": 75000,
            "order_id": "order_test_001",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["amount"] == 75000
        assert data["order_id"] == "order_test_001"
        assert "evt_" in data["event_id"]


class TestStatus:
    """GET /api/razorpay/status"""

    def test_status_returns_mode(self, auth_client):
        resp = auth_client.get("/api/razorpay/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data
        assert data["mode"] in ("live", "demo")

    def test_status_requires_auth(self, client):
        resp = client.get("/api/razorpay/status")
        assert resp.status_code in (401, 403)


class TestEventList:
    """GET /api/razorpay/events"""

    def test_list_events_empty(self, auth_client):
        resp = auth_client.get("/api/razorpay/events")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert "total" in data

    def test_list_events_after_simulate(self, auth_client):
        # Simulate an event first
        sim_resp = auth_client.post("/api/webhooks/razorpay/simulate", json={"amount": 50000})
        assert sim_resp.status_code == 200

        # List events
        resp = auth_client.get("/api/razorpay/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1


class TestProcessEvent:
    """POST /api/razorpay/events/{event_id}/process"""

    def test_process_creates_decision(self, auth_client):
        """Process a simulated event into evidence + decision."""
        # Simulate
        sim_resp = auth_client.post("/api/webhooks/razorpay/simulate", json={
            "event_type": "payment.captured",
            "amount": 100000,
        })
        event_id = sim_resp.json()["event_id"]

        # Process
        proc_resp = auth_client.post(f"/api/razorpay/events/{event_id}/process")
        assert proc_resp.status_code == 200
        data = proc_resp.json()
        assert data["status"] == "processed"
        assert data["gross_amount"] == 100000
        assert data["final_amount"] == 92000  # 8% platform fee
        assert data["decision_id"].startswith("dec_")
        assert data["evidence_id"].startswith("ev_")

    def test_duplicate_process_returns_409(self, auth_client):
        """Processing the same event twice returns 409."""
        sim_resp = auth_client.post("/api/webhooks/razorpay/simulate", json={"amount": 50000})
        event_id = sim_resp.json()["event_id"]

        # First process
        resp1 = auth_client.post(f"/api/razorpay/events/{event_id}/process")
        assert resp1.status_code == 200

        # Second process should be 409
        resp2 = auth_client.post(f"/api/razorpay/events/{event_id}/process")
        assert resp2.status_code == 409

    def test_process_nonexistent_event_returns_404(self, auth_client):
        resp = auth_client.post("/api/razorpay/events/nonexistent_event/process")
        assert resp.status_code == 404

    def test_processed_decision_verifiable(self, auth_client):
        """Decision created from Razorpay event can be verified."""
        sim_resp = auth_client.post("/api/webhooks/razorpay/simulate", json={"amount": 80000})
        event_id = sim_resp.json()["event_id"]
        proc_resp = auth_client.post(f"/api/razorpay/events/{event_id}/process")
        decision_id = proc_resp.json()["decision_id"]

        verify_resp = auth_client.get(f"/api/decisions/{decision_id}/verify")
        assert verify_resp.status_code == 200
        assert verify_resp.json()["valid"] is True


class TestSeedsVisible:
    """Verify seeded demo data is accessible through authenticated endpoints."""

    def test_decisions_list(self, auth_client):
        resp = auth_client.get("/api/decisions")
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("items", data)  # Handle paginated response
        assert len(items) >= 5  # At least 5 seeded decisions

    def test_policies_list(self, auth_client):
        resp = auth_client.get("/api/policies")
        assert resp.status_code == 200
        assert len(resp.json()) >= 4

    def test_stats(self, auth_client):
        resp = auth_client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_decisions"] >= 5
        assert data["verified_decisions"] >= 4
        assert data["flagged_decisions"] >= 1

    def test_scenarios_list(self, auth_client):
        resp = auth_client.get("/api/scenarios")
        assert resp.status_code == 200
        assert len(resp.json()) >= 5

    def test_decision_detail(self, auth_client):
        resp = auth_client.get("/api/decisions/dec_001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["gross_amount"] == 100000
        assert data["final_amount"] == 75000
        assert data["status"] == "APPROVED"

    def test_decision_evidence(self, auth_client):
        resp = auth_client.get("/api/decisions/dec_001/evidence")
        assert resp.status_code == 200
        assert len(resp.json()) >= 4

    def test_verify_all(self, auth_client):
        resp = auth_client.get("/api/decisions/verify-all")
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    def test_tampered_decision_fails(self, auth_client):
        resp = auth_client.get("/api/decisions/dec_005_tampered/verify")
        assert resp.status_code == 200
        assert resp.json()["valid"] is False

    def test_seller_decisions(self, auth_client):
        resp = auth_client.get("/api/sellers/seller_abc/decisions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_decisions"] >= 4

    def test_defense_packet(self, auth_client):
        resp = auth_client.get("/api/decisions/dec_001/defense-packet")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["evidence"]) >= 4
        assert data["integrity"]["valid"] is True
