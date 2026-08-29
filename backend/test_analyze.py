"""Tests for POST /api/decisions/analyze — user-created decision workflow.

All endpoints require authentication. Tests use the auth_client fixture
which sends JWT tokens automatically.
"""
import pytest


class TestAnalyzeDecisionEndpoint:

    def test_basic_analysis(self, auth_client):
        """Happy path: create a decision from order evidence."""
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 100000,
            "evidence_items": [
                {"source_type": "order", "raw_content": '{"order_id": "ORD-TEST-001", "seller_id": "seller_test", "amount": 100000}'},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "analyzed"
        assert data["gross_amount"] == 100000
        assert data["final_amount"] == 92000  # 8% platform fee
        assert data["decision_id"].startswith("dec_")
        assert len(data["evidence_ids"]) == 1

    def test_analysis_with_sla_breach(self, auth_client):
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 100000,
            "has_sla_breach": True,
            "sla_penalty_amount": 12000,
            "evidence_items": [
                {"source_type": "order", "raw_content": '{"amount": 100000}'},
                {"source_type": "delivery", "raw_content": '{"delay_days": 5}'},
            ],
        })
        assert resp.status_code == 200
        assert resp.json()["final_amount"] == 80000
        assert len(resp.json()["line_items"]) == 2

    def test_analysis_with_returns(self, auth_client):
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 100000,
            "has_returns": True,
            "return_reserve_amount": 5000,
            "evidence_items": [
                {"source_type": "order", "raw_content": '{"amount": 100000}'},
                {"source_type": "refund_record", "raw_content": '{"refund_amount": 5000}'},
            ],
        })
        assert resp.status_code == 200
        assert resp.json()["final_amount"] == 87000
        assert len(resp.json()["line_items"]) == 2

    def test_analysis_all_deductions(self, auth_client):
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 100000,
            "has_sla_breach": True,
            "sla_penalty_amount": 12000,
            "has_returns": True,
            "return_reserve_amount": 5000,
            "evidence_items": [
                {"source_type": "order", "raw_content": '{"amount": 100000}'},
                {"source_type": "delivery", "raw_content": '{"delay_days": 5}'},
                {"source_type": "refund_record", "raw_content": '{"refund_amount": 5000}'},
            ],
        })
        assert resp.status_code == 200
        assert resp.json()["final_amount"] == 75000
        assert len(resp.json()["line_items"]) == 3

    def test_rejects_zero_amount(self, auth_client):
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 0,
            "evidence_items": [{"source_type": "order", "raw_content": "{}"}],
        })
        assert resp.status_code == 400

    def test_rejects_negative_amount(self, auth_client):
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": -100,
            "evidence_items": [{"source_type": "order", "raw_content": "{}"}],
        })
        assert resp.status_code == 400

    def test_rejects_no_evidence(self, auth_client):
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 100000,
            "evidence_items": [],
        })
        assert resp.status_code == 400

    def test_rejects_without_auth(self, client):
        """Endpoint requires authentication."""
        resp = client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 100000,
            "evidence_items": [{"source_type": "order", "raw_content": "{}"}],
        })
        assert resp.status_code in (401, 403)

    def test_hash_is_valid(self, auth_client):
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 50000,
            "evidence_items": [{"source_type": "order", "raw_content": '{"amount": 50000}'}],
        })
        data = resp.json()
        assert data["decision_hash"]
        assert data["decision_hash"] != data["prev_decision_hash"]

    def test_analyzed_decision_verifiable(self, auth_client):
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 50000,
            "evidence_items": [{"source_type": "order", "raw_content": '{"amount": 50000}'}],
        })
        data = resp.json()
        verify_resp = auth_client.get(f"/api/decisions/{data['decision_id']}/verify")
        assert verify_resp.status_code == 200
        assert verify_resp.json()["checked_count"] >= 1

    def test_default_approver(self, auth_client):
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 50000,
            "evidence_items": [{"source_type": "order", "raw_content": '{"amount": 50000}'}],
        })
        assert resp.status_code == 200
        # Verify via API
        d = auth_client.get(f"/api/decisions/{resp.json()['decision_id']}").json()
        assert d["status"] == "REVIEW_REQUIRED"

    def test_model_output_source(self, auth_client):
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 50000,
            "evidence_items": [{"source_type": "order", "raw_content": '{"amount": 50000}'}],
        })
        assert resp.status_code == 200
        d = auth_client.get(f"/api/decisions/{resp.json()['decision_id']}").json()
        assert d["model_output"]["source"] == "user_analysis"

    def test_stats_updated_after_analyze(self, auth_client):
        stats_before = auth_client.get("/api/stats").json()
        auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 50000,
            "evidence_items": [{"source_type": "order", "raw_content": '{"amount": 50000}'}],
        })
        stats_after = auth_client.get("/api/stats").json()
        assert stats_after["total_decisions"] == stats_before["total_decisions"] + 1

    def test_defense_packet_for_analyzed_decision(self, auth_client):
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 50000,
            "evidence_items": [{"source_type": "order", "raw_content": '{"amount": 50000}'}],
        })
        decision_id = resp.json()["decision_id"]
        defense_resp = auth_client.get(f"/api/decisions/{decision_id}/defense-packet")
        assert defense_resp.status_code == 200
        packet = defense_resp.json()
        assert packet["decision"]["decision_id"] == decision_id
        assert packet["financial_breakdown"]["gross_amount"] == 50000

    def test_evidence_has_extracted_facts(self, auth_client):
        resp = auth_client.post("/api/decisions/analyze", json={
            "entity_id": "seller_test",
            "gross_amount": 50000,
            "evidence_items": [
                {"source_type": "order", "raw_content": '{"order_id": "ORD-001", "product": "Abaya"}'},
            ],
        })
        assert resp.status_code == 200
        evidence_id = resp.json()["evidence_ids"][0]
        ev_resp = auth_client.get(f"/api/evidence/{evidence_id}")
        assert ev_resp.status_code == 200
        facts = ev_resp.json()["extracted_facts"]
        assert len(facts) >= 2
