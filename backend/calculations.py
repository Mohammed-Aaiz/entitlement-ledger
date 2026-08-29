"""Deterministic financial calculation engine.

All amounts are calculated here using pure functions.
The LLM NEVER determines financial amounts.
"""
from typing import List, Optional
from models import LineItem


def calculate_platform_fee(gross_amount: int, fee_percentage: float = 0.08) -> int:
    """Calculate platform fee as percentage of gross amount."""
    return int(gross_amount * fee_percentage)


def calculate_sla_penalty(gross_amount: int, penalty_amount: int) -> int:
    """SLA penalty is a fixed amount determined by policy."""
    return penalty_amount


def calculate_return_reserve(gross_amount: int, reserve_percentage: float = 0.05) -> int:
    """Return reserve is a percentage held back."""
    return int(gross_amount * reserve_percentage)


def build_line_items(
    gross_amount: int,
    has_sla_breach: bool = False,
    sla_penalty_amount: int = 0,
    has_returns: bool = False,
    return_reserve_amount: int = 0,
    evidence_ids: dict = None,
) -> List[LineItem]:
    """Build deterministic line items for a payout decision."""
    if evidence_ids is None:
        evidence_ids = {}

    items = []

    # Platform fee always applies
    platform_fee = calculate_platform_fee(gross_amount)
    items.append(LineItem(
        label="Platform fee",
        amount=platform_fee,
        type="fee",
        policy_clause_id="platform_1_1",
        evidence_ids=evidence_ids.get("platform_fee", []),
    ))

    # SLA penalty only if breach is confirmed
    if has_sla_breach and sla_penalty_amount > 0:
        penalty = calculate_sla_penalty(gross_amount, sla_penalty_amount)
        items.append(LineItem(
            label="SLA penalty",
            amount=penalty,
            type="deduction",
            policy_clause_id="sla_4_2",
            evidence_ids=evidence_ids.get("sla_penalty", []),
        ))

    # Return reserve only if returns exist
    if has_returns and return_reserve_amount > 0:
        items.append(LineItem(
            label="Return reserve",
            amount=return_reserve_amount,
            type="deduction",
            policy_clause_id="returns_3_1",
            evidence_ids=evidence_ids.get("return_reserve", []),
        ))

    return items


def calculate_final_amount(gross_amount: int, line_items: List[LineItem]) -> int:
    """Calculate final settlement amount from gross and line items."""
    total_adjustments = sum(item.amount for item in line_items)
    final = gross_amount - total_adjustments
    return max(final, 0)  # Never go negative


def validate_calculation(
    gross_amount: int,
    line_items: List[LineItem],
    expected_final: int,
) -> dict:
    """Validate that a calculation is internally consistent."""
    calculated_final = calculate_final_amount(gross_amount, line_items)
    total_deductions = sum(
        item.amount for item in line_items
        if item.type in ("fee", "deduction")
    )
    total_credits = sum(
        item.amount for item in line_items
        if item.type == "credit"
    )

    return {
        "valid": calculated_final == expected_final,
        "calculated_final": calculated_final,
        "expected_final": expected_final,
        "gross_amount": gross_amount,
        "total_deductions": total_deductions,
        "total_credits": total_credits,
    }
