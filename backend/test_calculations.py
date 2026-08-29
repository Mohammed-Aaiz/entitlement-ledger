"""Tests for deterministic financial calculations and hash chain."""
import pytest
from calculations import (
    calculate_platform_fee,
    calculate_sla_penalty,
    calculate_return_reserve,
    calculate_final_amount,
    build_line_items,
    validate_calculation,
)
from hash_chain import compute_decision_hash, verify_chain, canonicalize
from models import LineItem


class TestFinancialCalculations:
    """Test all deterministic financial calculations."""

    def test_platform_fee_8_percent(self):
        fee = calculate_platform_fee(100000)
        assert fee == 8000

    def test_platform_fee_small_amount(self):
        fee = calculate_platform_fee(45000)
        assert fee == 3600

    def test_sla_penalty_fixed(self):
        penalty = calculate_sla_penalty(100000, 12000)
        assert penalty == 12000

    def test_return_reserve_5_percent(self):
        reserve = calculate_return_reserve(100000)
        assert reserve == 5000

    def test_final_amount_calculation(self):
        line_items = [
            LineItem(label="Platform fee", amount=8000, type="fee"),
            LineItem(label="SLA penalty", amount=12000, type="deduction"),
            LineItem(label="Return reserve", amount=5000, type="deduction"),
        ]
        final = calculate_final_amount(100000, line_items)
        assert final == 75000

    def test_final_amount_no_items(self):
        final = calculate_final_amount(100000, [])
        assert final == 100000

    def test_final_amount_never_negative(self):
        line_items = [
            LineItem(label="Huge fee", amount=200000, type="fee"),
        ]
        final = calculate_final_amount(100000, line_items)
        assert final == 0

    def test_build_line_items_full_scenario(self):
        evidence_ids = {
            "platform_fee": ["ev_order_001"],
            "sla_penalty": ["ev_delivery_001"],
            "return_reserve": ["ev_refund_001"],
        }
        items = build_line_items(
            gross_amount=100000,
            has_sla_breach=True,
            sla_penalty_amount=12000,
            has_returns=True,
            return_reserve_amount=5000,
            evidence_ids=evidence_ids,
        )
        assert len(items) == 3
        assert items[0].label == "Platform fee"
        assert items[0].amount == 8000
        assert items[0].type == "fee"
        assert items[1].label == "SLA penalty"
        assert items[1].amount == 12000
        assert items[2].label == "Return reserve"
        assert items[2].amount == 5000

    def test_build_line_items_no_sla_breach(self):
        items = build_line_items(
            gross_amount=100000,
            has_sla_breach=False,
            has_returns=False,
        )
        assert len(items) == 1  # Only platform fee
        assert items[0].label == "Platform fee"

    def test_build_line_items_sla_only(self):
        items = build_line_items(
            gross_amount=80000,
            has_sla_breach=True,
            sla_penalty_amount=12000,
            has_returns=False,
        )
        assert len(items) == 2
        assert items[0].label == "Platform fee"
        assert items[1].label == "SLA penalty"

    def test_validate_calculation_correct(self):
        items = [
            LineItem(label="Platform fee", amount=8000, type="fee"),
            LineItem(label="SLA penalty", amount=12000, type="deduction"),
            LineItem(label="Return reserve", amount=5000, type="deduction"),
        ]
        result = validate_calculation(100000, items, 75000)
        assert result["valid"] is True
        assert result["calculated_final"] == 75000

    def test_validate_calculation_incorrect(self):
        items = [
            LineItem(label="Platform fee", amount=8000, type="fee"),
        ]
        result = validate_calculation(100000, items, 75000)
        assert result["valid"] is False


class TestHashChain:
    """Test tamper-evident hash chain."""

    def test_genesis_hash_deterministic(self):
        data = {"decision_id": "test", "amount": 1000}
        h1 = compute_decision_hash(data, "genesis")
        h2 = compute_decision_hash(data, "genesis")
        assert h1 == h2

    def test_different_prev_hash_different_result(self):
        data = {"decision_id": "test", "amount": 1000}
        h1 = compute_decision_hash(data, "genesis")
        h2 = compute_decision_hash(data, "some_other_hash")
        assert h1 != h2

    def test_tampered_data_detected(self):
        data = {"decision_id": "test", "amount": 1000}
        original_hash = compute_decision_hash(data, "genesis")

        # Tamper with data
        data["amount"] = 999
        tampered_hash = compute_decision_hash(data, "genesis")
        assert original_hash != tampered_hash

    def test_chain_verification_valid(self):
        d1 = {"decision_id": "d1", "amount": 1000}
        d1["decision_hash"] = compute_decision_hash(d1, "genesis")

        d2 = {"decision_id": "d2", "amount": 2000}
        d2["decision_hash"] = compute_decision_hash(d2, d1["decision_hash"])

        result = verify_chain([d1, d2])
        assert result["valid"] is True
        assert result["checked_count"] == 2
        assert result["break_at"] is None

    def test_chain_verification_tampered(self):
        d1 = {"decision_id": "d1", "amount": 1000}
        d1["decision_hash"] = compute_decision_hash(d1, "genesis")

        d2 = {"decision_id": "d2", "amount": 2000}
        d2["decision_hash"] = compute_decision_hash(d2, d1["decision_hash"])

        # Tamper with d2's content but keep old hash
        d2["amount"] = 9999

        result = verify_chain([d1, d2])
        assert result["valid"] is False
        assert result["checked_count"] == 1
        assert result["break_at"] == "d2"

    def test_canonicalize_excludes_decision_hash(self):
        data = {"a": 1, "b": 2, "decision_hash": "abc123"}
        canonical = canonicalize(data)
        assert "decision_hash" not in canonical
        assert '"a":1' in canonical
        assert '"b":2' in canonical

    def test_canonicalize_sorted_keys(self):
        data = {"z": 1, "a": 2, "m": 3}
        canonical = canonicalize(data)
        assert canonical == '{"a":2,"m":3,"z":1}'

    def test_empty_chain_valid(self):
        result = verify_chain([])
        assert result["valid"] is True
        assert result["checked_count"] == 0
