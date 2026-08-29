"""Mock extraction and reasoning functions for offline testing.

These are NOT used in production — they only run when use_mock=True in the pipeline.
"""


def extract_facts_mock(
    evidence_id: str,
    source_type: str,
    raw_content: str,
) -> dict:
    """Mock extraction — returns pre-defined facts based on source type."""
    mock_facts = {
        "order": [
            {
                "fact_type": "order_detail",
                "value": f"Order {evidence_id} placed",
                "amount": None,
                "date": None,
                "evidence_quote": "Order record found",
            }
        ],
        "delivery": [
            {
                "fact_type": "delivery_record",
                "value": f"Delivery record {evidence_id}",
                "amount": None,
                "date": None,
                "evidence_quote": "Delivery record found",
            }
        ],
        "complaint": [
            {
                "fact_type": "complaint",
                "value": f"Complaint {evidence_id} filed",
                "amount": None,
                "date": None,
                "evidence_quote": "Complaint record found",
            }
        ],
        "refund_record": [
            {
                "fact_type": "refund",
                "value": f"Refund {evidence_id} processed",
                "amount": None,
                "date": None,
                "evidence_quote": "Refund record found",
            }
        ],
    }
    return {
        "facts": mock_facts.get(source_type, [
            {
                "fact_type": "other",
                "value": f"Evidence {evidence_id} of type {source_type}",
                "amount": None,
                "date": None,
                "evidence_quote": f"Record {evidence_id} found",
            }
        ])
    }


def reason_about_claims_mock(
    extracted_facts: dict,
    policies: list[dict],
    scenario_type: str = "clear",
) -> dict:
    """Mock reasoning — returns pre-defined claims based on scenario type."""
    if scenario_type == "clear":
        return {
            "claims": [
                {
                    "claim_type": "sla_breach",
                    "policy_clause_id": "sla_4_2",
                    "evidence_ids": ["ev_delivery_001", "ev_complaint_001"],
                    "reasoning": "Delivery was 5 days late (promised 2024-11-20, actual 2024-11-25). Customer complaint confirms dissatisfaction. Policy SLA-4.2 applies for delays of 3+ business days."
                },
                {
                    "claim_type": "return_processed",
                    "policy_clause_id": "returns_3_1",
                    "evidence_ids": ["ev_refund_001"],
                    "reasoning": "Return was processed due to delayed delivery. Policy Returns-3.1 allows reserve withholding for processed returns."
                }
            ],
            "classification": "clear",
            "confidence": 0.92,
            "reasoning_summary": "Evidence clearly supports SLA breach and return processing. Both deductions are well-documented."
        }
    elif scenario_type == "sla_only":
        return {
            "claims": [
                {
                    "claim_type": "sla_breach",
                    "policy_clause_id": "sla_4_2",
                    "evidence_ids": ["ev_delivery_002"],
                    "reasoning": "Delivery was 4 days late (promised 2024-11-23, actual 2024-11-27). Policy SLA-4.2 applies for delays of 3+ business days."
                }
            ],
            "classification": "clear",
            "confidence": 0.95,
            "reasoning_summary": "SLA breach clearly documented. No returns or additional issues."
        }
    elif scenario_type == "no_penalty":
        return {
            "claims": [],
            "classification": "clear",
            "confidence": 0.88,
            "reasoning_summary": "Customer complaint about color mismatch is low severity and resolved with goodwill credit. No SLA breach detected. No product return processed. Evidence does not support additional deductions beyond standard platform fee."
        }
    elif scenario_type == "no_issues":
        return {
            "claims": [],
            "classification": "clear",
            "confidence": 0.98,
            "reasoning_summary": "Order completed successfully with no issues. Standard platform fee applies."
        }
    else:
        return {
            "claims": [],
            "classification": "clear",
            "confidence": 0.90,
            "reasoning_summary": "No claims supported by evidence."
        }
