"""Deterministic finance engine for payment-to-settlement reconciliation.

ALL monetary values are integer currency subunits (paise).  Floating point
is never used for financial truth.

Core equation:
    expected_settlement = captured_amount - total_refunds - fees - taxes + adjustments

Then:
    variance = actual_settlement - expected_settlement

The engine returns a full calculation trace so every decision can be
independently audited.  The AI controller NEVER calls or overrides these
functions — this module is the sole authority on money.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Supported currencies for validation (subunit = 100 paise = 1 unit)
SUPPORTED_CURRENCIES = {"INR"}

# Integer overflow guard — amounts larger than this are rejected as malformed.
MAX_AMOUNT = 10_000_000_000_000  # 10^13 paise (₹100 billion)


class FinancialValidationError(ValueError):
    """Raised when monetary inputs are malformed or inconsistent."""


@dataclass
class SettlementCalculation:
    """Deterministic settlement calculation with full audit trace."""

    captured_amount: int
    refund_total: int
    fee_total: int
    tax_total: int
    adjustments: int  # positive adds to settlement, negative subtracts
    expected_settlement: int
    actual_settlement: Optional[int]
    variance: Optional[int]
    currency: str = "INR"
    steps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "captured_amount": self.captured_amount,
            "refund_total": self.refund_total,
            "fee_total": self.fee_total,
            "tax_total": self.tax_total,
            "adjustments": self.adjustments,
            "expected_settlement": self.expected_settlement,
            "actual_settlement": self.actual_settlement,
            "variance": self.variance,
            "currency": self.currency,
            "steps": list(self.steps),
            "formula": (
                f"{self.captured_amount} - {self.refund_total} - {self.fee_total} "
                f"- {self.tax_total} + {self.adjustments} = {self.expected_settlement}"
            ),
        }


def _validate_amount(amount: int, field_name: str) -> None:
    """Validate an amount is a non-negative integer within bounds."""
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise FinancialValidationError(
            f"{field_name} must be an integer (paise), got {type(amount).__name__}"
        )
    if amount < 0:
        raise FinancialValidationError(f"{field_name} cannot be negative: {amount}")
    if amount > MAX_AMOUNT:
        raise FinancialValidationError(
            f"{field_name} exceeds supported range: {amount}"
        )


def validate_currency(currency: str) -> str:
    """Validate a currency code; raises FinancialValidationError when unsupported."""
    if not currency:
        raise FinancialValidationError("Currency is required")
    currency = currency.upper().strip()
    if currency not in SUPPORTED_CURRENCIES:
        raise FinancialValidationError(
            f"Unsupported currency '{currency}'. Supported: {sorted(SUPPORTED_CURRENCIES)}"
        )
    return currency


def calculate_expected_settlement(
    captured_amount: int,
    refund_total: int = 0,
    fee_total: int = 0,
    tax_total: int = 0,
    adjustments: int = 0,
    currency: str = "INR",
    step_labels: Optional[dict] = None,
) -> SettlementCalculation:
    """Compute expected settlement deterministically and return a trace.

    Args:
        captured_amount: gross captured amount in paise.
        refund_total: total refunds in paise.
        fee_total: total fees in paise.
        tax_total: total taxes in paise.
        adjustments: net adjustments in paise (positive credits the merchant,
            negative debits).  Callers must pre-sum signed adjustments.
        currency: ISO currency code (INR supported).
        step_labels: optional human-readable labels per component for the trace.

    Raises:
        FinancialValidationError on any malformed input or a negative
        expected settlement (settlement can never be negative).

    Returns:
        SettlementCalculation with the full audit trace.
    """
    labels = step_labels or {}

    _validate_amount(captured_amount, "captured_amount")
    _validate_amount(refund_total, "refund_total")
    _validate_amount(fee_total, "fee_total")
    _validate_amount(tax_total, "tax_total")

    # Adjustments may be positive or negative; validate bounds only.
    if not isinstance(adjustments, int) or isinstance(adjustments, bool):
        raise FinancialValidationError(
            f"adjustments must be an integer (paise), got {type(adjustments).__name__}"
        )
    if abs(adjustments) > MAX_AMOUNT:
        raise FinancialValidationError(f"adjustments exceed supported range: {adjustments}")

    currency = validate_currency(currency)

    # Refunds can never exceed the captured amount.
    if refund_total > captured_amount:
        raise FinancialValidationError(
            f"Invalid refund total: {refund_total} paise refunded against "
            f"{captured_amount} paise captured"
        )

    expected = captured_amount - refund_total - fee_total - tax_total + adjustments
    if expected < 0:
        raise FinancialValidationError(
            f"Expected settlement cannot be negative: {expected} paise "
            f"(captured={captured_amount}, refunds={refund_total}, "
            f"fees={fee_total}, taxes={tax_total}, adjustments={adjustments})"
        )

    steps = [
        {
            "component": "captured_amount",
            "sign": "+",
            "amount": captured_amount,
            "running_total": captured_amount,
            "label": labels.get("captured", "Gross captured amount"),
        },
        {
            "component": "refund_total",
            "sign": "-",
            "amount": refund_total,
            "running_total": captured_amount - refund_total,
            "label": labels.get("refunds", "Total refunds"),
        },
        {
            "component": "fee_total",
            "sign": "-",
            "amount": fee_total,
            "running_total": captured_amount - refund_total - fee_total,
            "label": labels.get("fees", "Total fees"),
        },
        {
            "component": "tax_total",
            "sign": "-",
            "amount": tax_total,
            "running_total": captured_amount - refund_total - fee_total - tax_total,
            "label": labels.get("taxes", "Total taxes"),
        },
        {
            "component": "adjustments",
            "sign": "+" if adjustments >= 0 else "-",
            "amount": abs(adjustments),
            "running_total": expected,
            "label": labels.get("adjustments", "Net adjustments"),
        },
    ]

    return SettlementCalculation(
        captured_amount=captured_amount,
        refund_total=refund_total,
        fee_total=fee_total,
        tax_total=tax_total,
        adjustments=adjustments,
        expected_settlement=expected,
        actual_settlement=None,
        variance=None,
        currency=currency,
        steps=steps,
    )


def compare_settlement(
    calculation: SettlementCalculation,
    actual_settlement: int,
) -> SettlementCalculation:
    """Attach the actual settlement and compute the variance.

    variance = actual_settlement - expected_settlement

    A zero variance means the books reconcile.  A non-zero variance is a
    genuine financial discrepancy — never silently absorbed.
    """
    _validate_amount(actual_settlement, "actual_settlement")
    calculation.actual_settlement = actual_settlement
    calculation.variance = actual_settlement - calculation.expected_settlement
    return calculation


def calculate_variance(expected_settlement: int, actual_settlement: int) -> int:
    """Pure variance computation."""
    _validate_amount(expected_settlement, "expected_settlement")
    _validate_amount(actual_settlement, "actual_settlement")
    return actual_settlement - expected_settlement