"""Deterministic synthetic case generator for Finance Controller benchmark.

Generates reproducible finance cases with known ground truth.
No LLM involvement — pure deterministic generation from a seed.
"""
import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Optional


# ── Case categories ─────────────────────────────────────────────────
CATEGORY_CLEAN_PAYMENT = "clean_payment"
CATEGORY_DELIVERED_ON_TIME = "delivered_on_time"
CATEGORY_LATE_DELIVERY = "late_delivery"
CATEGORY_PARTIAL_DELIVERY = "partial_delivery"
CATEGORY_REFUND = "refund"
CATEGORY_RETURN = "return"
CATEGORY_MISSING_DELIVERY = "missing_delivery"
CATEGORY_CONFLICTING_EVIDENCE = "conflicting_evidence"
CATEGORY_FEE_MISMATCH = "fee_mismatch"
CATEGORY_DUPLICATE_EVENT = "duplicate_event"

ALL_CATEGORIES = [
    CATEGORY_CLEAN_PAYMENT,
    CATEGORY_DELIVERED_ON_TIME,
    CATEGORY_LATE_DELIVERY,
    CATEGORY_PARTIAL_DELIVERY,
    CATEGORY_REFUND,
    CATEGORY_RETURN,
    CATEGORY_MISSING_DELIVERY,
    CATEGORY_CONFLICTING_EVIDENCE,
    CATEGORY_FEE_MISMATCH,
    CATEGORY_DUPLICATE_EVENT,
]

# Default distribution (100 cases)
DEFAULT_DISTRIBUTION = {
    CATEGORY_CLEAN_PAYMENT: 20,
    CATEGORY_DELIVERED_ON_TIME: 15,
    CATEGORY_LATE_DELIVERY: 15,
    CATEGORY_PARTIAL_DELIVERY: 10,
    CATEGORY_REFUND: 10,
    CATEGORY_RETURN: 10,
    CATEGORY_MISSING_DELIVERY: 5,
    CATEGORY_CONFLICTING_EVIDENCE: 5,
    CATEGORY_FEE_MISMATCH: 5,
    CATEGORY_DUPLICATE_EVENT: 5,
}

# ── Policies ────────────────────────────────────────────────────────
POLICIES = {
    "platform_1_1": {
        "policy_id": "platform_1_1",
        "version": "2.1",
        "clause_text": "Platform Fee: A flat 8% commission is charged on gross seller entitlement.",
        "effective_date": "2024-01-01",
    },
    "sla_4_2": {
        "policy_id": "sla_4_2",
        "version": "3.0",
        "clause_text": "SLA Penalty: Delays of 3+ business days incur a fixed penalty of 12000.",
        "effective_date": "2024-06-01",
    },
    "returns_3_1": {
        "policy_id": "returns_3_1",
        "version": "2.0",
        "clause_text": "Return Reserve: A reserve equal to the return amount is withheld for 14 business days.",
        "effective_date": "2024-03-01",
    },
}


@dataclass
class SyntheticCase:
    """A single benchmark case with all inputs and expected outputs."""
    case_id: str
    category: str
    tenant_id: str = "benchmark"
    order_id: str = ""
    payment_id: str = ""
    gross_amount: int = 0
    payment_status: str = "captured"
    delivery_delay_days: Optional[int] = None
    has_refund: bool = False
    refund_amount: int = 0
    has_return: bool = False
    return_amount: int = 0
    has_complaint: bool = False
    complaint_severity: str = "low"
    missing_delivery_evidence: bool = False
    conflicting_evidence: bool = False
    fee_mismatch: bool = False
    duplicate_event: bool = False
    applicable_policy_ids: list = field(default_factory=list)
    evidence_records: list = field(default_factory=list)
    # Ground truth
    expected_claims: list = field(default_factory=list)
    expected_classification: str = "clear"
    expected_final_amount: int = 0
    expected_decision_status: str = "REVIEW_REQUIRED"
    expected_exceptions: list = field(default_factory=list)


def _deterministic_id(rng: random.Random, prefix: str) -> str:
    """Generate a deterministic ID from the RNG state."""
    h = hashlib.sha256(f"{prefix}_{rng.randint(0, 2**32)}".encode()).hexdigest()[:12]
    return f"{prefix}_{h}"


def _generate_order_evidence(case: SyntheticCase, rng: random.Random) -> dict:
    """Generate order evidence record."""
    ev_id = _deterministic_id(rng, "ev_order")
    content = {
        "order_id": case.order_id,
        "seller_id": f"seller_{case.case_id}",
        "amount": case.gross_amount,
        "order_date": "2024-11-15",
        "status": "captured",
    }
    return {
        "evidence_id": ev_id,
        "source_type": "order",
        "raw_content": json.dumps(content),
        "extracted_facts": json.dumps([{"fact": f"Order {case.order_id}", "confidence": 1.0}]),
        "linked_decision_ids": json.dumps([]),
    }


def _generate_delivery_evidence(case: SyntheticCase, rng: random.Random) -> Optional[dict]:
    """Generate delivery evidence record (or None if missing)."""
    if case.missing_delivery_evidence:
        return None
    if case.delivery_delay_days is None:
        return None

    ev_id = _deterministic_id(rng, "ev_delivery")
    delay = case.delivery_delay_days
    promised = "2024-11-20"
    actual = f"2024-11-{20 + delay:02d}" if delay > 0 else "2024-11-20"

    content = {
        "order_id": case.order_id,
        "promised_date": promised,
        "actual_date": actual,
        "delay_days": delay,
        "carrier": "Express Logistics",
    }
    return {
        "evidence_id": ev_id,
        "source_type": "delivery",
        "raw_content": json.dumps(content),
        "extracted_facts": json.dumps([{"fact": f"Delivery delay {delay} days", "confidence": 1.0}]),
        "linked_decision_ids": json.dumps([]),
    }


def _generate_refund_evidence(case: SyntheticCase, rng: random.Random) -> Optional[dict]:
    """Generate refund evidence record (or None)."""
    if not case.has_refund:
        return None

    ev_id = _deterministic_id(rng, "ev_refund")
    content = {
        "refund_id": f"REF_{case.case_id}",
        "order_id": case.order_id,
        "amount": case.refund_amount,
        "reason": "Customer return due to delayed delivery",
        "status": "processed",
        "return_date": "2024-11-27",
    }
    return {
        "evidence_id": ev_id,
        "source_type": "refund_record",
        "raw_content": json.dumps(content),
        "extracted_facts": json.dumps([{"fact": f"Refund {case.refund_amount}", "confidence": 1.0}]),
        "linked_decision_ids": json.dumps([]),
    }


def _generate_complaint_evidence(case: SyntheticCase, rng: random.Random) -> Optional[dict]:
    """Generate complaint evidence record (or None)."""
    if not case.has_complaint:
        return None

    ev_id = _deterministic_id(rng, "ev_complaint")
    content = {
        "complaint_id": f"CMP_{case.case_id}",
        "order_id": case.order_id,
        "customer_id": f"cust_{case.case_id}",
        "issue": "Late delivery causing customer dissatisfaction",
        "severity": case.complaint_severity,
        "resolution": "Partial refund offered",
        "filed_date": "2024-11-22",
    }
    return {
        "evidence_id": ev_id,
        "source_type": "complaint",
        "raw_content": json.dumps(content),
        "extracted_facts": json.dumps([{"fact": f"Complaint severity {case.complaint_severity}", "confidence": 1.0}]),
        "linked_decision_ids": json.dumps([]),
    }


def _generate_conflicting_evidence(case: SyntheticCase, rng: random.Random) -> Optional[dict]:
    """Generate a conflicting delivery evidence (delay says 0 but complaint says late)."""
    if not case.conflicting_evidence:
        return None

    ev_id = _deterministic_id(rng, "ev_conflict")
    content = {
        "order_id": case.order_id,
        "promised_date": "2024-11-20",
        "actual_date": "2024-11-20",
        "delay_days": 0,
        "carrier": "Express Logistics",
    }
    return {
        "evidence_id": ev_id,
        "source_type": "delivery",
        "raw_content": json.dumps(content),
        "extracted_facts": json.dumps([{"fact": "Delivery on time (conflicting)", "confidence": 0.5}]),
        "linked_decision_ids": json.dumps([]),
    }


def generate_cases(
    count: int = 100,
    seed: int = 42,
    distribution: Optional[dict] = None,
) -> list[SyntheticCase]:
    """Generate a deterministic set of synthetic benchmark cases.

    Args:
        count: Total number of cases to generate.
        seed: Deterministic seed for reproducibility.
        distribution: Category → count mapping. If None, scales DEFAULT_DISTRIBUTION.

    Returns:
        List of SyntheticCase with ground truth populated.
    """
    rng = random.Random(seed)
    dist = distribution or DEFAULT_DISTRIBUTION

    # Scale distribution to target count
    total = sum(dist.values())
    scaled = {}
    remaining = count
    for i, (cat, n) in enumerate(dist.items()):
        if i == len(dist) - 1:
            scaled[cat] = remaining
        else:
            scaled[cat] = max(1, round(n * count / total))
            remaining -= scaled[cat]

    cases = []
    for category, n in sorted(scaled.items()):
        for i in range(n):
            case = _generate_single_case(category, i, rng)
            cases.append(case)

    return cases


def _generate_single_case(category: str, index: int, rng: random.Random) -> SyntheticCase:
    """Generate a single case for a given category."""
    case_id = f"bench_{category}_{index:03d}"
    order_id = f"ORD_{case_id}"
    payment_id = f"PAY_{case_id}"

    # Realistic INR amounts
    gross = rng.choice([25000, 40000, 50000, 75000, 80000, 100000, 120000, 150000, 200000])

    case = SyntheticCase(
        case_id=case_id,
        category=category,
        order_id=order_id,
        payment_id=payment_id,
        gross_amount=gross,
    )

    if category == CATEGORY_CLEAN_PAYMENT:
        case.payment_status = "captured"
        case.applicable_policy_ids = ["platform_1_1"]
        case.expected_classification = "clear"
        case.expected_decision_status = "APPROVED"

    elif category == CATEGORY_DELIVERED_ON_TIME:
        case.delivery_delay_days = 0
        case.applicable_policy_ids = ["platform_1_1"]
        case.expected_classification = "clear"
        case.expected_decision_status = "APPROVED"

    elif category == CATEGORY_LATE_DELIVERY:
        case.delivery_delay_days = rng.choice([3, 4, 5, 7, 10])
        case.has_complaint = True
        case.complaint_severity = rng.choice(["low", "medium", "high"])
        case.applicable_policy_ids = ["platform_1_1", "sla_4_2"]
        case.expected_classification = "clear"

    elif category == CATEGORY_PARTIAL_DELIVERY:
        case.delivery_delay_days = rng.choice([1, 2])
        case.applicable_policy_ids = ["platform_1_1"]
        case.expected_classification = "clear"

    elif category == CATEGORY_REFUND:
        case.delivery_delay_days = rng.choice([3, 5])
        case.has_refund = True
        case.refund_amount = int(gross * rng.uniform(0.05, 0.30))
        case.has_complaint = True
        case.applicable_policy_ids = ["platform_1_1", "sla_4_2", "returns_3_1"]

    elif category == CATEGORY_RETURN:
        case.delivery_delay_days = rng.choice([4, 6])
        case.has_return = True
        case.return_amount = int(gross * rng.uniform(0.10, 0.40))
        case.has_refund = True
        case.refund_amount = case.return_amount
        case.applicable_policy_ids = ["platform_1_1", "sla_4_2", "returns_3_1"]

    elif category == CATEGORY_MISSING_DELIVERY:
        case.missing_delivery_evidence = True
        case.applicable_policy_ids = ["platform_1_1"]
        case.expected_classification = "exception"

    elif category == CATEGORY_CONFLICTING_EVIDENCE:
        case.delivery_delay_days = 0
        case.conflicting_evidence = True
        case.has_complaint = True
        case.complaint_severity = "high"
        case.applicable_policy_ids = ["platform_1_1", "sla_4_2"]
        case.expected_classification = "ambiguous"

    elif category == CATEGORY_FEE_MISMATCH:
        case.delivery_delay_days = rng.choice([3, 5])
        case.fee_mismatch = True
        case.applicable_policy_ids = ["platform_1_1", "sla_4_2"]
        case.expected_classification = "exception"

    elif category == CATEGORY_DUPLICATE_EVENT:
        case.delivery_delay_days = rng.choice([3, 4])
        case.duplicate_event = True
        case.applicable_policy_ids = ["platform_1_1", "sla_4_2"]

    # Generate evidence records
    evidence = []
    order_ev = _generate_order_evidence(case, rng)
    evidence.append(order_ev)

    delivery_ev = _generate_delivery_evidence(case, rng)
    if delivery_ev:
        evidence.append(delivery_ev)

    refund_ev = _generate_refund_evidence(case, rng)
    if refund_ev:
        evidence.append(refund_ev)

    complaint_ev = _generate_complaint_evidence(case, rng)
    if complaint_ev:
        evidence.append(complaint_ev)

    conflict_ev = _generate_conflicting_evidence(case, rng)
    if conflict_ev:
        evidence.append(conflict_ev)

    case.evidence_records = evidence

    # Compute ground truth
    _compute_ground_truth(case)

    return case


def _compute_ground_truth(case: SyntheticCase):
    """Compute deterministic ground truth for a case."""
    from calculations import (
        calculate_platform_fee,
        calculate_final_amount,
        build_line_items,
    )

    gross = case.gross_amount
    has_sla = case.delivery_delay_days is not None and case.delivery_delay_days >= 3
    sla_penalty = 12000 if has_sla else 0
    has_ret = case.has_return
    ret_amount = case.return_amount if has_ret else 0

    # Expected claims
    claims = []
    if has_sla:
        claims.append({
            "claim_type": "sla_breach",
            "policy_clause_id": "sla_4_2",
            "reasoning": f"Delivery was {case.delivery_delay_days} days late",
        })
    if case.has_refund and case.refund_amount > 0:
        claims.append({
            "claim_type": "return_processed",
            "policy_clause_id": "returns_3_1",
            "reasoning": f"Return/refund of {case.refund_amount} processed",
        })

    case.expected_claims = claims

    # Expected classification
    if case.expected_classification == "":
        case.expected_classification = "clear"

    # Expected final amount (deterministic) — must match pipeline's fixed amounts
    # Pipeline uses: SLA penalty = 12000 (fixed), return reserve = 5000 (fixed)
    pipeline_sla_penalty = 12000 if has_sla else 0
    pipeline_return_reserve = 5000 if has_ret else 0
    line_items = build_line_items(
        gross_amount=gross,
        has_sla_breach=has_sla,
        sla_penalty_amount=pipeline_sla_penalty,
        has_returns=has_ret,
        return_reserve_amount=pipeline_return_reserve,
    )
    case.expected_final_amount = calculate_final_amount(gross, line_items)

    # Expected exceptions
    if case.missing_delivery_evidence:
        case.expected_exceptions.append("MISSING_EVIDENCE")
    if case.conflicting_evidence:
        case.expected_exceptions.append("CONFLICTING_EVIDENCE")
    if case.fee_mismatch:
        case.expected_exceptions.append("DATA_INCONSISTENCY")


def compute_ground_truth_fingerprint(
    tenant_id: str,
    scenario_id: str,
    evidence_ids: list[str],
    policy_ids: list[str],
) -> str:
    """Compute the analysis fingerprint for ground truth comparison."""
    from ai.pipeline import compute_analysis_fingerprint
    return compute_analysis_fingerprint(
        tenant_id=tenant_id,
        scenario_id=scenario_id,
        evidence_ids=sorted(evidence_ids),
        policy_ids=sorted(policy_ids),
    )
