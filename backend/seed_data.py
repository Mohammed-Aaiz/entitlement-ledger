"""Seed data: 5 scenarios for the EntitlementLedger MVP."""
import uuid
import json
from datetime import datetime, timedelta
from calculations import build_line_items, calculate_final_amount
from hash_chain import compute_decision_hash


def _now() -> str:
    return datetime.now().isoformat()


def _past(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


# ============================================================
# EVIDENCE RECORDS
# ============================================================
EVIDENCE_RECORDS = [
    # --- Scenario 1: Return + SLA Breach ---
    {
        "evidence_id": "ev_order_001",
        "source_type": "order",
        "raw_content": json.dumps({
            "order_id": "ORD-2024-7891",
            "seller_id": "seller_abc",
            "product": "Embroidered Abaya",
            "amount": 100000,
            "order_date": "2024-11-15",
            "status": "delivered_with_issues",
        }),
        "extracted_facts": json.dumps([
            {"fact": "Order placed for ₹100,000", "confidence": 1.0},
            {"fact": "Seller: seller_abc", "confidence": 1.0},
            {"fact": "Status: delivered with issues", "confidence": 1.0},
        ]),
        "linked_decision_ids": json.dumps(["dec_001"]),
    },
    {
        "evidence_id": "ev_delivery_001",
        "source_type": "delivery",
        "raw_content": json.dumps({
            "order_id": "ORD-2024-7891",
            "promised_date": "2024-11-20",
            "actual_date": "2024-11-25",
            "delay_days": 5,
            "carrier": "Express Logistics",
        }),
        "extracted_facts": json.dumps([
            {"fact": "Promised delivery: 2024-11-20", "confidence": 1.0},
            {"fact": "Actual delivery: 2024-11-25", "confidence": 1.0},
            {"fact": "Delay: 5 days beyond SLA", "confidence": 1.0},
        ]),
        "linked_decision_ids": json.dumps(["dec_001"]),
    },
    {
        "evidence_id": "ev_complaint_001",
        "source_type": "complaint",
        "raw_content": json.dumps({
            "complaint_id": "CMP-4421",
            "order_id": "ORD-2024-7891",
            "customer_id": "cust_xyz",
            "issue": "Late delivery causing customer dissatisfaction",
            "severity": "high",
            "resolution": "Partial refund offered to customer",
            "filed_date": "2024-11-22",
        }),
        "extracted_facts": json.dumps([
            {"fact": "Customer complaint filed for late delivery", "confidence": 1.0},
            {"fact": "Severity: high", "confidence": 1.0},
            {"fact": "Partial refund offered to customer", "confidence": 1.0},
        ]),
        "linked_decision_ids": json.dumps(["dec_001"]),
    },
    {
        "evidence_id": "ev_refund_001",
        "source_type": "refund_record",
        "raw_content": json.dumps({
            "refund_id": "REF-8812",
            "order_id": "ORD-2024-7891",
            "amount": 5000,
            "reason": "Customer return due to delayed delivery",
            "status": "processed",
            "return_date": "2024-11-27",
        }),
        "extracted_facts": json.dumps([
            {"fact": "Return processed for ₹5,000", "confidence": 1.0},
            {"fact": "Reason: delayed delivery", "confidence": 1.0},
            {"fact": "Status: processed", "confidence": 1.0},
        ]),
        "linked_decision_ids": json.dumps(["dec_001"]),
    },
    # --- Scenario 2: Late Delivery Only ---
    {
        "evidence_id": "ev_order_002",
        "source_type": "order",
        "raw_content": json.dumps({
            "order_id": "ORD-2024-7892",
            "seller_id": "seller_def",
            "product": "Kanjivaram Silk Saree",
            "amount": 80000,
            "order_date": "2024-11-18",
            "status": "delivered",
        }),
        "extracted_facts": json.dumps([
            {"fact": "Order placed for ₹80,000", "confidence": 1.0},
            {"fact": "Seller: seller_def", "confidence": 1.0},
            {"fact": "Status: delivered", "confidence": 1.0},
        ]),
        "linked_decision_ids": json.dumps(["dec_002"]),
    },
    {
        "evidence_id": "ev_delivery_002",
        "source_type": "delivery",
        "raw_content": json.dumps({
            "order_id": "ORD-2024-7892",
            "promised_date": "2024-11-23",
            "actual_date": "2024-11-27",
            "delay_days": 4,
            "carrier": "Standard Post",
        }),
        "extracted_facts": json.dumps([
            {"fact": "Promised delivery: 2024-11-23", "confidence": 1.0},
            {"fact": "Actual delivery: 2024-11-27", "confidence": 1.0},
            {"fact": "Delay: 4 days beyond SLA", "confidence": 1.0},
        ]),
        "linked_decision_ids": json.dumps(["dec_002"]),
    },
    # --- Scenario 3: Complaint but No Penalty ---
    {
        "evidence_id": "ev_order_003",
        "source_type": "order",
        "raw_content": json.dumps({
            "order_id": "ORD-2024-7893",
            "seller_id": "seller_abc",
            "product": "Prayer Mat Velvet",
            "amount": 45000,
            "order_date": "2024-11-20",
            "status": "delivered",
        }),
        "extracted_facts": json.dumps([
            {"fact": "Order placed for ₹45,000", "confidence": 1.0},
            {"fact": "Seller: seller_abc", "confidence": 1.0},
            {"fact": "Status: delivered on time", "confidence": 1.0},
        ]),
        "linked_decision_ids": json.dumps(["dec_003"]),
    },
    {
        "evidence_id": "ev_complaint_003",
        "source_type": "complaint",
        "raw_content": json.dumps({
            "complaint_id": "CMP-4423",
            "order_id": "ORD-2024-7893",
            "customer_id": "cust_abc",
            "issue": "Color mismatch reported by customer",
            "severity": "low",
            "resolution": "Customer kept product with ₹500 goodwill credit",
            "filed_date": "2024-11-28",
        }),
        "extracted_facts": json.dumps([
            {"fact": "Customer reported color mismatch", "confidence": 1.0},
            {"fact": "Severity: low", "confidence": 1.0},
            {"fact": "Resolved with goodwill credit, no return", "confidence": 1.0},
            {"fact": "No SLA breach involved", "confidence": 1.0},
        ]),
        "linked_decision_ids": json.dumps(["dec_003"]),
    },
    # --- Scenario 4: Multiple decisions (uses ev_order_001 + new ones) ---
    {
        "evidence_id": "ev_order_004",
        "source_type": "order",
        "raw_content": json.dumps({
            "order_id": "ORD-2024-7894",
            "seller_id": "seller_abc",
            "product": "Halal Perfume Oud",
            "amount": 35000,
            "order_date": "2024-12-01",
            "status": "completed",
        }),
        "extracted_facts": json.dumps([
            {"fact": "Order placed for ₹35,000", "confidence": 1.0},
            {"fact": "Seller: seller_abc", "confidence": 1.0},
            {"fact": "Status: completed successfully", "confidence": 1.0},
        ]),
        "linked_decision_ids": json.dumps(["dec_004"]),
    },
    # --- Scenario 5: Tampered decision (reuses scenario 1 evidence) ---
]

# ============================================================
# POLICY RECORDS
# ============================================================
POLICY_RECORDS = [
    {
        "policy_id": "platform_1_1",
        "version": "2.1",
        "clause_text": "Platform Fee: A flat 8% commission is charged on gross seller entitlement for all completed transactions. This fee covers platform infrastructure, payment processing, and marketplace services.",
        "effective_date": "2024-01-01",
    },
    {
        "policy_id": "sla_4_2",
        "version": "3.0",
        "clause_text": "SLA Penalty: Sellers must deliver within the promised delivery window. Delays of 3+ business days incur a fixed penalty of ₹12,000 per affected order. Delays of 1-2 days incur ₹5,000. The penalty is non-negotiable once an SLA breach is confirmed by delivery records.",
        "effective_date": "2024-06-01",
    },
    {
        "policy_id": "returns_3_1",
        "version": "2.0",
        "clause_text": "Return Reserve: When a product return is processed within the return window, a reserve equal to the return amount is withheld from the seller payout for 14 business days to cover potential secondary return processing. Reserve is released if no further claims arise.",
        "effective_date": "2024-03-01",
    },
    {
        "policy_id": "dispute_5_1",
        "version": "1.0",
        "clause_text": "Dispute Resolution: Sellers may raise a query on any financial decision within 30 days. A complete Defense Packet including all evidence, policy clauses, and verification results will be provided.",
        "effective_date": "2024-01-01",
    },
]


def _build_decision_1() -> dict:
    """Scenario 1: Return + SLA breach — primary scenario."""
    evidence_ids_map = {
        "platform_fee": ["ev_order_001"],
        "sla_penalty": ["ev_delivery_001", "ev_complaint_001"],
        "return_reserve": ["ev_refund_001"],
    }
    line_items = build_line_items(
        gross_amount=100000,
        has_sla_breach=True,
        sla_penalty_amount=12000,
        has_returns=True,
        return_reserve_amount=5000,
        evidence_ids=evidence_ids_map,
    )
    final_amount = calculate_final_amount(100000, line_items)
    now = _now()
    decision_data = {
        "decision_id": "dec_001",
        "entity_type": "seller",
        "entity_id": "seller_abc",
        "gross_amount": 100000,
        "line_items": [item.model_dump() for item in line_items],
        "final_amount": final_amount,
        "policy_version_id": "platform_1_1,sla_4_2,returns_3_1",
        "approver_id": "finance_mgr_01",
        "approved_at": _past(5),
        "model_output": {
            "claims": [
                {"type": "sla_breach", "evidence_ids": ["ev_delivery_001"], "policy_clause_id": "sla_4_2"},
                {"type": "return_processed", "evidence_ids": ["ev_refund_001"], "policy_clause_id": "returns_3_1"},
            ]
        },
        "prev_decision_hash": "genesis",
        "decision_hash": "",
        "created_at": now,
        "status": "APPROVED",
    }
    decision_data["decision_hash"] = compute_decision_hash(decision_data, "genesis")
    return decision_data


def _build_decision_2() -> dict:
    """Scenario 2: Late delivery only."""
    evidence_ids_map = {
        "platform_fee": ["ev_order_002"],
        "sla_penalty": ["ev_delivery_002"],
    }
    line_items = build_line_items(
        gross_amount=80000,
        has_sla_breach=True,
        sla_penalty_amount=12000,
        has_returns=False,
        evidence_ids=evidence_ids_map,
    )
    final_amount = calculate_final_amount(80000, line_items)
    now = _now()
    decision_data = {
        "decision_id": "dec_002",
        "entity_type": "seller",
        "entity_id": "seller_def",
        "gross_amount": 80000,
        "line_items": [item.model_dump() for item in line_items],
        "final_amount": final_amount,
        "policy_version_id": "platform_1_1,sla_4_2",
        "approver_id": "finance_mgr_01",
        "approved_at": _past(3),
        "model_output": {
            "claims": [
                {"type": "sla_breach", "evidence_ids": ["ev_delivery_002"], "policy_clause_id": "sla_4_2"},
            ]
        },
        "prev_decision_hash": "genesis",
        "decision_hash": "",
        "created_at": now,
        "status": "APPROVED",
    }
    decision_data["decision_hash"] = compute_decision_hash(decision_data, "genesis")
    return decision_data


def _build_decision_3() -> dict:
    """Scenario 3: Complaint but NO penalty justified."""
    evidence_ids_map = {
        "platform_fee": ["ev_order_003"],
    }
    line_items = build_line_items(
        gross_amount=45000,
        has_sla_breach=False,
        has_returns=False,
        evidence_ids=evidence_ids_map,
    )
    final_amount = calculate_final_amount(45000, line_items)
    now = _now()
    decision_data = {
        "decision_id": "dec_003",
        "entity_type": "seller",
        "entity_id": "seller_abc",
        "gross_amount": 45000,
        "line_items": [item.model_dump() for item in line_items],
        "final_amount": final_amount,
        "policy_version_id": "platform_1_1",
        "approver_id": "finance_mgr_02",
        "approved_at": _past(2),
        "model_output": {
            "claims": [],
            "analysis": "Customer complaint was about color mismatch (severity: low). No SLA breach detected. No product return processed. Policy does not support additional deduction for minor complaints resolved with goodwill credit. Recommendation: platform fee only."
        },
        "prev_decision_hash": "genesis",
        "decision_hash": "",
        "created_at": now,
        "status": "APPROVED",
    }
    decision_data["decision_hash"] = compute_decision_hash(decision_data, "genesis")
    return decision_data


def _build_decision_4() -> dict:
    """Scenario 4: Second decision for same seller (seller_abc)."""
    evidence_ids_map = {
        "platform_fee": ["ev_order_004"],
    }
    line_items = build_line_items(
        gross_amount=35000,
        has_sla_breach=False,
        has_returns=False,
        evidence_ids=evidence_ids_map,
    )
    final_amount = calculate_final_amount(35000, line_items)
    now = _now()
    decision_data = {
        "decision_id": "dec_004",
        "entity_type": "seller",
        "entity_id": "seller_abc",
        "gross_amount": 35000,
        "line_items": [item.model_dump() for item in line_items],
        "final_amount": final_amount,
        "policy_version_id": "platform_1_1",
        "approver_id": "finance_mgr_01",
        "approved_at": _past(1),
        "model_output": {
            "claims": [],
            "analysis": "Order completed successfully. No penalties or reserves apply. Standard platform fee deduction."
        },
        "prev_decision_hash": "genesis",
        "decision_hash": "",
        "created_at": now,
        "status": "APPROVED",
    }
    decision_data["decision_hash"] = compute_decision_hash(decision_data, "genesis")
    return decision_data


def _build_decision_5_tampered() -> dict:
    """Scenario 5: A decision that is deliberately tampered with after hashing."""
    evidence_ids_map = {
        "platform_fee": ["ev_order_001"],
        "sla_penalty": ["ev_delivery_001", "ev_complaint_001"],
        "return_reserve": ["ev_refund_001"],
    }
    line_items = build_line_items(
        gross_amount=100000,
        has_sla_breach=True,
        sla_penalty_amount=12000,
        has_returns=True,
        return_reserve_amount=5000,
        evidence_ids=evidence_ids_map,
    )
    final_amount = calculate_final_amount(100000, line_items)
    now = _now()
    decision_data = {
        "decision_id": "dec_005_tampered",
        "entity_type": "seller",
        "entity_id": "seller_abc",
        "gross_amount": 100000,
        "line_items": [item.model_dump() for item in line_items],
        "final_amount": final_amount,
        "policy_version_id": "platform_1_1,sla_4_2,returns_3_1",
        "approver_id": "finance_mgr_01",
        "approved_at": _past(7),
        "model_output": {
            "claims": [
                {"type": "sla_breach", "evidence_ids": ["ev_delivery_001"], "policy_clause_id": "sla_4_2"},
                {"type": "return_processed", "evidence_ids": ["ev_refund_001"], "policy_clause_id": "returns_3_1"},
            ]
        },
        "prev_decision_hash": "genesis",
        "decision_hash": "",
        "created_at": now,
        "status": "APPROVED",
    }
    # Compute the valid hash first
    decision_data["decision_hash"] = compute_decision_hash(decision_data, "genesis")

    # TAMPER: change the final_amount after hashing
    decision_data["final_amount"] = 82000  # Changed from 75000
    decision_data["line_items"][1]["amount"] = 10000  # SLA penalty altered from 12000

    return decision_data


def get_all_scenarios() -> list[dict]:
    """Return the 5 seed scenarios."""
    return [
        {
            "scenario_id": "scenario_1",
            "name": "Return + SLA Breach",
            "description": "Platform fee, SLA penalty for late delivery, and return reserve for processed return.",
            "status": "completed",
        },
        {
            "scenario_id": "scenario_2",
            "name": "Late Delivery Only",
            "description": "Platform fee and SLA penalty for delivery delay. No returns.",
            "status": "completed",
        },
        {
            "scenario_id": "scenario_3",
            "name": "Complaint Without Penalty",
            "description": "Customer complaint filed but evidence does not justify additional deduction beyond platform fee.",
            "status": "completed",
        },
        {
            "scenario_id": "scenario_4",
            "name": "Multiple Seller Decisions",
            "description": "Second decision for seller_abc showing decision history on seller profile.",
            "status": "completed",
        },
        {
            "scenario_id": "scenario_5",
            "name": "Tampered Decision",
            "description": "A decision where stored content was modified after hashing, breaking the integrity chain.",
            "status": "completed",
        },
    ]


def get_all_decisions() -> list[dict]:
    """Return all 5 seeded decisions in hash-chain order."""
    d1 = _build_decision_1()
    d2 = _build_decision_2()
    d3 = _build_decision_3()
    d4 = _build_decision_4()
    d5 = _build_decision_5_tampered()

    # Chain d1 -> d2 -> d3 -> d4
    d2["prev_decision_hash"] = d1["decision_hash"]
    d2["decision_hash"] = compute_decision_hash(d2, d1["decision_hash"])

    d3["prev_decision_hash"] = d2["decision_hash"]
    d3["decision_hash"] = compute_decision_hash(d3, d2["decision_hash"])

    d4["prev_decision_hash"] = d3["decision_hash"]
    d4["decision_hash"] = compute_decision_hash(d4, d3["decision_hash"])

    # d5 is standalone (tampered, its hash doesn't match after alteration)
    return [d1, d2, d3, d4, d5]


def get_all_evidence() -> list[dict]:
    """Return all evidence records."""
    return EVIDENCE_RECORDS


def get_all_policies() -> list[dict]:
    """Return all policy records."""
    return POLICY_RECORDS


# ============================================================
# SCENARIO-EVIDENCE MAPPING
# ============================================================
# Maps scenario_id to the evidence records that belong to it
# This allows the AI pipeline to know which evidence to process
SCENARIO_EVIDENCE_MAP = {
    "scenario_1": ["ev_order_001", "ev_delivery_001", "ev_complaint_001", "ev_refund_001"],
    "scenario_2": ["ev_order_002", "ev_delivery_002"],
    "scenario_3": ["ev_order_003", "ev_complaint_003"],
    "scenario_4": ["ev_order_004"],
    "scenario_5": ["ev_order_001", "ev_delivery_001", "ev_complaint_001", "ev_refund_001"],
}

# Maps scenario_id to applicable policy IDs
SCENARIO_POLICY_MAP = {
    "scenario_1": ["platform_1_1", "sla_4_2", "returns_3_1"],
    "scenario_2": ["platform_1_1", "sla_4_2"],
    "scenario_3": ["platform_1_1"],
    "scenario_4": ["platform_1_1"],
    "scenario_5": ["platform_1_1", "sla_4_2", "returns_3_1"],
}


def get_scenario_evidence(scenario_id: str) -> list[dict]:
    """Get evidence records for a specific scenario."""
    evidence_ids = SCENARIO_EVIDENCE_MAP.get(scenario_id, [])
    all_evidence = get_all_evidence()
    return [ev for ev in all_evidence if ev["evidence_id"] in evidence_ids]


def get_scenario_policies(scenario_id: str) -> list[dict]:
    """Get policy records for a specific scenario."""
    policy_ids = SCENARIO_POLICY_MAP.get(scenario_id, [])
    all_policies = get_all_policies()
    return [p for p in all_policies if p["policy_id"] in policy_ids]


def get_last_decision_hash() -> str:
    """Get the hash of the last non-tampered decision in the chain."""
    decisions = get_all_decisions()
    # Find the last valid decision (not tampered)
    valid_decisions = [d for d in decisions if d["decision_id"] != "dec_005_tampered"]
    if valid_decisions:
        return valid_decisions[-1]["decision_hash"]
    return "genesis"
