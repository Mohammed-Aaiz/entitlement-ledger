"""Tests for Razorpay → EntitlementLedger integration.

Tests deterministic fact extraction, evidence creation, decision processing,
and provenance chain integrity.
"""
import pytest
from razorpay_adapter import extract_razorpay_facts, razorpay_event_to_evidence, process_razorpay_event_to_decision
from seed_data import get_all_policies


def _make_payment_event(event_id="evt_test_001", amount=100000, status="captured", source="local_simulator"):
    return {
        "event_id": event_id,
        "event_type": "payment.captured",
        "source": source,
        "verification_status": "unverified" if source == "local_simulator" else "verified",
        "razorpay_entity_type": "payment",
        "razorpay_entity_id": f"pay_{event_id}",
        "payment_id": f"pay_{event_id}",
        "order_id": f"order_{event_id}",
        "amount": amount,
        "currency": "INR",
        "status": status,
        "payload_hash": f"hash_{event_id}",
        "raw_payload": {
            "payload": {
                "payment": {"entity": {"id": f"pay_{event_id}", "amount": amount, "currency": "INR", "status": status, "order_id": f"order_{event_id}"}},
                "order": {"entity": {"id": f"order_{event_id}", "amount": amount, "currency": "INR"}},
            }
        },
    }


class TestFactExtraction:
    """Deterministic fact extraction from Razorpay payloads."""

    def test_extracts_payment_facts(self):
        event = _make_payment_event(amount=100000)
        facts = extract_razorpay_facts(event)
        assert len(facts) >= 4
        fact_text = " ".join(f["fact"] for f in facts)
        assert "pay_evt_test_001" in fact_text
        assert "100000" in fact_text
        assert "captured" in fact_text

    def test_extracts_order_facts(self):
        event = _make_payment_event()
        facts = extract_razorpay_facts(event)
        fact_text = " ".join(f["fact"] for f in facts)
        assert "order_evt_test_001" in fact_text

    def test_extracts_verification_fact(self):
        event = _make_payment_event(source="live_webhook")
        event["verification_status"] = "verified"
        facts = extract_razorpay_facts(event)
        fact_text = " ".join(f["fact"] for f in facts)
        assert "verified" in fact_text.lower() or "HMAC" in fact_text

    def test_refund_event_facts(self):
        event = {
            "event_id": "evt_refund_001",
            "event_type": "refund.created",
            "source": "local_simulator",
            "verification_status": "unverified",
            "razorpay_entity_type": "refund",
            "razorpay_entity_id": "rfnd_001",
            "payment_id": "pay_001",
            "order_id": "order_001",
            "amount": 5000,
            "currency": "INR",
            "status": "processed",
            "payload_hash": "",
            "raw_payload": {"payload": {"refund": {"entity": {"id": "rfnd_001", "amount": 5000, "payment_id": "pay_001", "status": "processed"}}}},
        }
        facts = extract_razorpay_facts(event)
        fact_text = " ".join(f["fact"] for f in facts)
        assert "rfnd_001" in fact_text
        assert "5000" in fact_text

    def test_all_facts_have_confidence(self):
        event = _make_payment_event()
        facts = extract_razorpay_facts(event)
        for f in facts:
            assert "confidence" in f
            assert 0.0 <= f["confidence"] <= 1.0


class TestEvidenceCreation:
    """Event → Evidence record conversion."""

    def test_creates_valid_evidence(self):
        event = _make_payment_event()
        evidence = razorpay_event_to_evidence(event)
        assert evidence["evidence_id"].startswith("ev_")
        assert evidence["source_type"] == "order"
        assert evidence["extracted_facts"]
        assert isinstance(evidence["raw_content"], str)

    def test_evidence_preserves_payload(self):
        event = _make_payment_event(amount=75000)
        evidence = razorpay_event_to_evidence(event)
        import json
        content = json.loads(evidence["raw_content"])
        assert content["razorpay_event_id"] == "evt_test_001"
        assert content["amount"] == 75000

    def test_refund_evidence_source_type(self):
        event = _make_payment_event()
        event["razorpay_entity_type"] = "refund"
        evidence = razorpay_event_to_evidence(event)
        assert evidence["source_type"] == "refund_record"


class TestDecisionCreation:
    """Event → Decision with deterministic calculation."""

    def test_creates_decision_with_platform_fee(self):
        event = _make_payment_event(amount=100000)
        evidence = razorpay_event_to_evidence(event)
        policies = get_all_policies()
        decision = process_razorpay_event_to_decision(event, evidence, policies)
        assert decision["gross_amount"] == 100000
        assert decision["final_amount"] == 92000
        assert decision["decision_hash"]

    def test_decision_model_output_has_razorpay_source(self):
        event = _make_payment_event()
        evidence = razorpay_event_to_evidence(event)
        policies = get_all_policies()
        decision = process_razorpay_event_to_decision(event, evidence, policies)
        assert decision["model_output"]["source"] == "razorpay"

    def test_rejects_zero_amount(self):
        event = _make_payment_event(amount=0)
        evidence = razorpay_event_to_evidence(event)
        with pytest.raises(ValueError, match="amount is 0"):
            process_razorpay_event_to_decision(event, evidence, get_all_policies())

    def test_decision_links_evidence(self):
        event = _make_payment_event()
        evidence = razorpay_event_to_evidence(event)
        decision = process_razorpay_event_to_decision(event, evidence, get_all_policies())
        linked = evidence.get("linked_decision_ids", [])
        assert decision["decision_id"] in linked


class TestEndToEnd:
    """Full flow: simulate → process → verify → defense packet."""

    def test_full_flow(self, auth_client):
        # 1. Simulate event
        sim = auth_client.post("/api/webhooks/razorpay/simulate", json={"amount": 100000})
        assert sim.status_code == 200
        event_id = sim.json()["event_id"]

        # 2. Process into ledger
        proc = auth_client.post(f"/api/razorpay/events/{event_id}/process")
        assert proc.status_code == 200
        decision_id = proc.json()["decision_id"]

        # 3. Verify hash chain
        verify = auth_client.get(f"/api/decisions/{decision_id}/verify")
        assert verify.status_code == 200
        assert verify.json()["valid"] is True

        # 4. Check defense packet
        defense = auth_client.get(f"/api/decisions/{decision_id}/defense-packet")
        assert defense.status_code == 200
        packet = defense.json()
        assert packet["decision"]["decision_id"] == decision_id
        assert packet["financial_breakdown"]["gross_amount"] == 100000

        # 5. Check stats updated
        stats = auth_client.get("/api/stats").json()
        assert stats["total_decisions"] >= 6  # 5 seeded + 1 new

    def test_tampered_decision_still_detected(self, auth_client):
        """Tampered decision is still detected even after new Razorpay decisions."""
        resp = auth_client.get("/api/decisions/dec_005_tampered/verify")
        assert resp.json()["valid"] is False

        # Add a new Razorpay decision
        sim = auth_client.post("/api/webhooks/razorpay/simulate", json={"amount": 50000})
        proc = auth_client.post(f"/api/razorpay/events/{sim.json()['event_id']}/process")
        assert proc.status_code == 200

        # Tampered decision still fails
        resp2 = auth_client.get("/api/decisions/dec_005_tampered/verify")
        assert resp2.json()["valid"] is False

    def test_duplicate_event_idempotent(self, auth_client):
        """Processing the same event twice doesn't create duplicate decisions."""
        sim = auth_client.post("/api/webhooks/razorpay/simulate", json={"amount": 60000})
        event_id = sim.json()["event_id"]

        # First process
        proc1 = auth_client.post(f"/api/razorpay/events/{event_id}/process")
        assert proc1.status_code == 200
        decision_id = proc1.json()["decision_id"]

        # Second process
        proc2 = auth_client.post(f"/api/razorpay/events/{event_id}/process")
        assert proc2.status_code == 409

        # Decision count didn't increase by 2
        resp = auth_client.get("/api/decisions").json()
        decisions = resp.get("items", resp)  # Handle both paginated and non-paginated
        matching = [d for d in decisions if d["decision_id"] == decision_id]
        assert len(matching) == 1
