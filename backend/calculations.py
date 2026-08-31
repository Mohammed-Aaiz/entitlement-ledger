"""Deterministic financial calculation engine.

All amounts are calculated here using pure functions.
The LLM NEVER determines financial amounts.
"""
from typing import List, Optional
from models import LineItem


# ── Exception categories ─────────────────────────────────────────────
# These are structured exception types for unresolved finance cases.
# An unresolved exception MUST result in REVIEW_REQUIRED status.
EXCEPTION_MISSING_EVIDENCE = "MISSING_EVIDENCE"
EXCEPTION_POLICY_AMBIGUITY = "POLICY_AMBIGUITY"
EXCEPTION_CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
EXCEPTION_LOW_CONFIDENCE = "LOW_CONFIDENCE"
EXCEPTION_DATA_INCONSISTENCY = "DATA_INCONSISTENCY"
EXCEPTION_CALCULATION_EXCEPTION = "CALCULATION_EXCEPTION"

ALL_EXCEPTION_CATEGORIES = {
    EXCEPTION_MISSING_EVIDENCE,
    EXCEPTION_POLICY_AMBIGUITY,
    EXCEPTION_CONFLICTING_EVIDENCE,
    EXCEPTION_LOW_CONFIDENCE,
    EXCEPTION_DATA_INCONSISTENCY,
    EXCEPTION_CALCULATION_EXCEPTION,
}


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


# ── Calculation trace ───────────────────────────────────────────────
# Each trace entry captures exactly how a single line item was derived.
# This is the finance-grade auditability layer.

def build_calculation_trace(
    gross_amount: int,
    line_items: List[LineItem],
    final_amount: int,
) -> dict:
    """Build a structured calculation trace for a financial decision.

    Returns a dict with:
      - gross_amount: the starting amount
      - steps: list of per-line-item trace dicts
      - total_deductions: sum of all fee/deduction amounts
      - total_credits: sum of all credit amounts
      - final_amount: the computed settlement amount
      - formula: the overall formula (gross - deductions + credits)
      - validated: whether the trace is internally consistent
    """
    steps = []
    for item in line_items:
        step = {
            "label": item.label,
            "calculation_type": _classify_calculation_type(item),
            "base_amount": gross_amount,
            "rate": _extract_rate(item, gross_amount),
            "formula": _build_formula(item, gross_amount),
            "calculated_amount": item.amount,
            "policy_clause_id": item.policy_clause_id,
            "evidence_ids": item.evidence_ids,
        }
        steps.append(step)

    total_deductions = sum(
        item.amount for item in line_items if item.type in ("fee", "deduction")
    )
    total_credits = sum(
        item.amount for item in line_items if item.type == "credit"
    )

    expected_final = gross_amount - total_deductions + total_credits

    return {
        "gross_amount": gross_amount,
        "steps": steps,
        "total_deductions": total_deductions,
        "total_credits": total_credits,
        "final_amount": final_amount,
        "formula": (
            f"{gross_amount} - {total_deductions}"
            + (f" + {total_credits}" if total_credits else "")
            + f" = {final_amount}"
        ),
        "validated": expected_final == final_amount,
    }


def _classify_calculation_type(item: LineItem) -> str:
    """Classify a line item into a calculation type for the trace."""
    if item.policy_clause_id and "platform" in (item.policy_clause_id or ""):
        return "percentage_fee"
    if item.policy_clause_id and "sla" in (item.policy_clause_id or ""):
        return "fixed_penalty"
    if item.policy_clause_id and "return" in (item.policy_clause_id or ""):
        return "percentage_reserve"
    if item.type == "fee":
        return "fee"
    if item.type == "deduction":
        return "deduction"
    if item.type == "credit":
        return "credit"
    return "unknown"


def _extract_rate(item: LineItem, gross_amount: int) -> Optional[float]:
    """Extract the effective rate for a line item, if applicable."""
    if gross_amount <= 0:
        return None
    if item.policy_clause_id and "platform" in (item.policy_clause_id or ""):
        # Platform fee is 8% by default
        return item.amount / gross_amount if item.amount else None
    if item.policy_clause_id and "return" in (item.policy_clause_id or ""):
        return item.amount / gross_amount if item.amount else None
    return None


def _build_formula(item: LineItem, gross_amount: int) -> str:
    """Build a human-readable formula string for a line item."""
    if item.policy_clause_id and "platform" in (item.policy_clause_id or ""):
        rate = _extract_rate(item, gross_amount)
        if rate is not None:
            return f"{gross_amount} * {rate}"
        return f"{gross_amount} * fee_rate"
    if item.policy_clause_id and "sla" in (item.policy_clause_id or ""):
        return f"fixed_penalty({item.amount})"
    if item.policy_clause_id and "return" in (item.policy_clause_id or ""):
        rate = _extract_rate(item, gross_amount)
        if rate is not None:
            return f"{gross_amount} * {rate}"
        return f"{gross_amount} * reserve_rate"
    return f"amount={item.amount}"
