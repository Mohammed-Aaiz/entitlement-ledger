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


# ===========================================================================
# Tier 3/4 — settlement recon linkage (deterministic, never guessed)
# ===========================================================================

class TestSettlementReconLinkage:
    """API-synced settlements carry no payment_id in their own payload; the
    deterministic link comes from razorpay settlement recon data.  Linkage
    must be exact — never guessed — and unlinked settlements must surface as
    auditable evidence instead of being dropped or mis-attached."""

    def _run(self, statements):
        import asyncio
        import database

        async def _execute():
            db = await database.get_db()
            try:
                for sql, params in statements:
                    await db.execute(sql, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.new_event_loop().run_until_complete(_execute())

    def _records(self, tenant="demo"):
        import asyncio
        from reconciliation.service import records_from_razorpay_async

        async def _build():
            return await records_from_razorpay_async(tenant)

        return asyncio.new_event_loop().run_until_complete(_build())

    def _payments_sql(self):
        return [
            ("INSERT INTO razorpay_payments (payment_id, tenant_id, order_id, entity_id, amount, currency, status, method, captured, amount_refunded, raw_payload, first_seen_at, last_synced_at) VALUES (?, 'demo', ?, 'payment', ?, 'INR', 'captured', 'card', 1, 0, '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')", (pid, oid, amount))
            for pid, oid, amount in self._payments
        ]

    def _settlements_sql(self):
        return [
            ("INSERT INTO razorpay_settlements (settlement_id, tenant_id, amount, currency, status, raw_payload, first_seen_at, last_synced_at) VALUES (?, 'demo', ?, 'INR', 'processed', '{}', '2026-01-03T00:00:00+00:00', '2026-01-03T00:00:00+00:00')", (sid, amount))
            for sid, amount in self._settlements
        ]

    def _recon_sql(self):
        return [
            ("INSERT INTO razorpay_settlement_recon (recon_id, tenant_id, settlement_id, payment_id, order_id, amount, fee, tax, recon_type, recorded_at) VALUES (?, 'demo', ?, ?, ?, ?, ?, ?, ?, ?)", (rid, sid, pid, oid, amount, fee, tax, rtype, "2026-01-03T00:00:00+00:00"))
            for rid, sid, pid, oid, amount, fee, tax, rtype in self._recon
        ]

    def test_direct_payment_recon_row_links_settlement(self):
        self._payments = [("pay_link_1", "ord_link_1", 100000)]
        self._settlements = [("setl_link_1", 97900)]
        self._recon = [("setl_link_1:payment:pay_link_1", "setl_link_1", "pay_link_1", "ord_link_1", 97900, 1800, 300, "payment")]
        self._run(self._payments_sql() + self._settlements_sql() + self._recon_sql())

        recs = self._records()
        settlements = [r for r in recs if r["record_type"] == "settlement" and r["external_id"] == "setl_link_1"]
        assert len(settlements) == 1
        s = settlements[0]
        assert s["payment_id"] == "pay_link_1", "recon payment row must link settlement to payment"
        assert s.get("extra", {}).get("unlinked_settlement") is not True

        fees = [r for r in recs if r["record_type"] == "fee_tax" and r["payment_id"] == "pay_link_1"]
        assert len(fees) == 1
        assert fees[0]["fee_amount"] == 1800 and fees[0]["tax_amount"] == 300

    def test_order_level_recon_links_when_unambiguous(self):
        self._payments = [("pay_link_a", "ord_link_x", 100000), ("pay_link_b", "ord_link_y", 50000)]
        self._settlements = [("setl_link_2", 47900)]
        self._recon = [("setl_link_2:order:ord_link_y", "setl_link_2", "", "ord_link_y", 0, 0, 0, "order")]
        self._run(self._payments_sql() + self._settlements_sql() + self._recon_sql())

        recs = self._records()
        s = [r for r in recs if r["record_type"] == "settlement" and r["external_id"] == "setl_link_2"][0]
        assert s["payment_id"] == "pay_link_b"

    def test_ambiguous_order_never_guessed(self):
        # Two payments share one order and only order-level recon exists —
        # attaching to the "first" payment would be a guess.
        self._payments = [("pay_link_c", "ord_link_z", 100000), ("pay_link_d", "ord_link_z", 70000)]
        self._settlements = [("setl_link_3", 97900)]
        self._recon = [("setl_link_3:order:ord_link_z", "setl_link_3", "", "ord_link_z", 0, 0, 0, "order")]
        self._run(self._payments_sql() + self._settlements_sql() + self._recon_sql())

        recs = self._records()
        s = [r for r in recs if r["record_type"] == "settlement" and r["external_id"] == "setl_link_3"][0]
        assert s["payment_id"] == "setl_link_3", "ambiguous order-level recon must stay unlinked"
        assert s["extra"].get("unlinked_settlement") is True

    def test_unlinked_settlement_is_preserved_not_dropped(self):
        self._payments = []
        self._settlements = [("setl_link_4", 50000)]
        self._recon = []
        self._run(self._payments_sql() + self._settlements_sql() + self._recon_sql())

        recs = self._records()
        s = [r for r in recs if r["record_type"] == "settlement" and r["external_id"] == "setl_link_4"][0]
        assert s["payment_id"] == "setl_link_4"
        assert s["extra"].get("unlinked_settlement") is True


class TestReconPayloadMapping:
    """Razorpay recon payload → deterministic linkage rows."""

    def test_payment_item_uses_entity_id(self):
        from razorpay_routes import _recon_rows_from_payload
        payload = {"items": [{
            "entity_id": "pay_map_1", "type": "payment", "payment_id": None,
            "order_id": "ord_map_1", "amount": 97100, "fee": 2900, "tax": 0,
            "settlement_id": "setl_map_1",
        }]}
        rows = _recon_rows_from_payload("setl_map_1", payload)
        assert len(rows) == 1
        assert rows[0]["payment_id"] == "pay_map_1"
        assert rows[0]["order_id"] == "ord_map_1"
        assert rows[0]["fee"] == 2900
        assert rows[0]["recon_type"] == "payment"

    def test_refund_item_keeps_payment_id(self):
        from razorpay_routes import _recon_rows_from_payload
        payload = {"items": [{
            "entity_id": "rfnd_map_1", "type": "refund", "payment_id": "pay_map_2",
            "order_id": None, "amount": 242500, "fee": 0, "tax": 0,
            "settlement_id": "setl_map_2",
        }]}
        rows = _recon_rows_from_payload("setl_map_2", payload)
        assert len(rows) == 1
        assert rows[0]["payment_id"] == "pay_map_2"
        assert rows[0]["recon_type"] == "refund"

    def test_transfer_and_unreferenced_items_skipped(self):
        from razorpay_routes import _recon_rows_from_payload
        payload = {"items": [
            {"entity_id": "trf_1", "type": "transfer", "payment_id": "pay_map_3",
             "order_id": None, "amount": 100296, "fee": 296, "tax": 46},
            {"entity_id": "adj_1", "type": "adjustment", "payment_id": None,
             "order_id": None, "amount": 1012, "fee": 0, "tax": 0},
            {"entity_id": "item_map_4", "type": "payment", "payment_id": None,
             "order_id": None, "amount": 5000, "fee": 0, "tax": 0},
        ]}
        rows = _recon_rows_from_payload("setl_map_3", payload)
        # Transfer rows are a separate financial context; an item with no
        # resolvable payment/entity/order reference cannot be linked.
        assert rows == []
