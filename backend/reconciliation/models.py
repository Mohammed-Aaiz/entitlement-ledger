"""Domain models for the reconciliation subsystem.

Records are normalized financial facts (payments, refunds, settlements,
fees/taxes, adjustments) with integer-paise amounts.  A reconciliation
case groups related records and holds the deterministic outcome.  A run
is a batch execution producing many cases plus aggregate metrics.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# Record types
RECORD_PAYMENT = "payment"
RECORD_REFUND = "refund"
RECORD_SETTLEMENT = "settlement"
RECORD_FEE_TAX = "fee_tax"
RECORD_ADJUSTMENT = "adjustment"
# Tier 5-7 context record types (non-monetary evidence).  These records
# NEVER enter the deterministic financial calculation — the calculator
# only reads payment/refund/settlement/fee_tax/adjustment amounts.  They
# exist to give disputes, invoices, payment links, and operational events
# a real, evidence-backed place in a reconciliation case.
RECORD_DISPUTE = "dispute"
RECORD_INVOICE = "invoice"
RECORD_PAYMENT_LINK = "payment_link"
RECORD_OPERATIONAL = "operational"

ALL_RECORD_TYPES = {
    RECORD_PAYMENT,
    RECORD_REFUND,
    RECORD_SETTLEMENT,
    RECORD_FEE_TAX,
    RECORD_ADJUSTMENT,
    RECORD_DISPUTE,
    RECORD_INVOICE,
    RECORD_PAYMENT_LINK,
    RECORD_OPERATIONAL,
}

# Context (non-financial-calculation) record types — matched into the case
# for tier analysis but never summed by the calculator.
CONTEXT_RECORD_TYPES = {
    RECORD_DISPUTE,
    RECORD_INVOICE,
    RECORD_PAYMENT_LINK,
    RECORD_OPERATIONAL,
}

# Reconciliation tiers a record type participates in.
RECORD_TIER = {
    RECORD_PAYMENT: 1,
    RECORD_REFUND: 2,
    RECORD_SETTLEMENT: 3,
    RECORD_FEE_TAX: 4,
    RECORD_DISPUTE: 5,
    RECORD_INVOICE: 6,
    RECORD_PAYMENT_LINK: 6,
    RECORD_OPERATIONAL: 7,
}

# Classification outcomes (top-level decision gate results)
CLASS_MATCHED = "MATCHED"
CLASS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
CLASS_EXCEPTION = "EXCEPTION"

ALL_CLASSIFICATIONS = {CLASS_MATCHED, CLASS_REVIEW_REQUIRED, CLASS_EXCEPTION}

# AI status values
AI_NOT_ATTEMPTED = "not_attempted"
AI_NOT_NEEDED = "not_needed"
AI_AVAILABLE = "available"
AI_UNAVAILABLE = "unavailable"
AI_FAILED = "failed"

ALL_AI_STATUSES = {AI_NOT_ATTEMPTED, AI_NOT_NEEDED, AI_AVAILABLE, AI_UNAVAILABLE, AI_FAILED}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class FinancialRecord:
    """A normalized financial record (payment/refund/settlement/fee_tax/adjustment)."""

    record_type: str
    external_id: str  # payment_id / refund_id / settlement_id / reference
    amount: int  # paise; always positive magnitude
    currency: str = "INR"
    status: str = "unknown"
    payment_id: str = ""
    order_id: str = ""
    # fee_tax records
    fee_amount: int = 0
    tax_amount: int = 0
    # adjustment records: positive | negative
    adjustment_sign: str = ""
    # business timestamp (event time / created / settled), authoritative ordering
    recorded_at: Optional[str] = None
    source: str = "batch"  # live_webhook | api_sync | batch | fixture
    raw_evidence_ref: str = ""
    payload_hash: str = ""
    extra: dict = field(default_factory=dict)
    record_id: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.record_id:
            self.record_id = new_id("rec")
        if not self.created_at:
            self.created_at = _now_iso()

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "record_type": self.record_type,
            "external_id": self.external_id,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status,
            "payment_id": self.payment_id,
            "order_id": self.order_id,
            "fee_amount": self.fee_amount,
            "tax_amount": self.tax_amount,
            "adjustment_sign": self.adjustment_sign,
            "recorded_at": self.recorded_at,
            "source": self.source,
            "raw_evidence_ref": self.raw_evidence_ref,
            "payload_hash": self.payload_hash,
            "extra": self.extra,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FinancialRecord":
        rec = cls(
            record_type=data.get("record_type", ""),
            external_id=data.get("external_id", ""),
            amount=int(data.get("amount", 0)),
            currency=data.get("currency", "INR"),
            status=data.get("status", "unknown"),
            payment_id=data.get("payment_id", ""),
            order_id=data.get("order_id", ""),
            fee_amount=int(data.get("fee_amount", 0) or 0),
            tax_amount=int(data.get("tax_amount", 0) or 0),
            adjustment_sign=data.get("adjustment_sign", ""),
            recorded_at=data.get("recorded_at"),
            source=data.get("source", "batch"),
            raw_evidence_ref=data.get("raw_evidence_ref", ""),
            payload_hash=data.get("payload_hash", ""),
            extra=data.get("extra", {}),
        )
        rec.record_id = data.get("record_id") or new_id("rec")
        rec.created_at = data.get("created_at") or _now_iso()
        return rec


@dataclass
class ReconciliationCase:
    """One payment's full reconciliation: related records + deterministic outcome."""

    payment_id: str
    run_id: str = ""
    classification: str = CLASS_REVIEW_REQUIRED
    expected_amount: int = 0
    actual_amount: int = 0
    variance: int = 0
    exception_codes: list[str] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)
    ai_status: str = AI_NOT_ATTEMPTED
    ai_invoked: bool = False
    ai_confidence: Optional[float] = None
    ai_interpretation: dict = field(default_factory=dict)
    ai_technical_reason: str = ""
    ai_trigger_reason: str = ""
    ai_tool_calls: int = 0
    calculation_trace: dict = field(default_factory=dict)
    match_info: dict = field(default_factory=dict)
    decision_id: str = ""
    explanation: str = ""
    records: list[FinancialRecord] = field(default_factory=list)
    # Tier 1-7 findings attached to this case (structured, deterministic).
    tier_findings: list[dict] = field(default_factory=list)
    tiers_applied: list[int] = field(default_factory=list)
    # Typed, evidence-backed relationships reconstructed for this case.
    relationships: list[dict] = field(default_factory=list)
    case_id: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.case_id:
            self.case_id = new_id("rcase")
        if not self.created_at:
            self.created_at = _now_iso()

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "payment_id": self.payment_id,
            "run_id": self.run_id,
            "classification": self.classification,
            "expected_amount": self.expected_amount,
            "actual_amount": self.actual_amount,
            "variance": self.variance,
            "exception_codes": list(self.exception_codes),
            "exceptions": list(self.exceptions),
            "ai_status": self.ai_status,
            "ai_invoked": self.ai_invoked,
            "ai_confidence": self.ai_confidence,
            "ai_interpretation": self.ai_interpretation,
            "ai_technical_reason": self.ai_technical_reason,
            "ai_trigger_reason": self.ai_trigger_reason,
            "ai_tool_calls": self.ai_tool_calls,
            "calculation_trace": self.calculation_trace,
            "match_info": self.match_info,
            "decision_id": self.decision_id,
            "explanation": self.explanation,
            "created_at": self.created_at,
            "related_record_ids": [r.record_id for r in self.records],
            "tier_findings": list(self.tier_findings),
            "tiers_applied": sorted(set(self.tiers_applied)),
            "relationships": list(self.relationships),
        }

    def to_storage_dict(self) -> dict:
        d = self.to_dict()
        # For DB persistence, also store records separately (see service)
        return d


@dataclass
class ReconciliationRun:
    """A batch reconciliation execution with aggregate metrics."""

    run_id: str = ""
    status: str = "running"  # running | completed | failed
    source: str = "batch"
    total_records: int = 0
    total_cases: int = 0
    matched: int = 0
    review_required: int = 0
    exceptions: int = 0
    match_rate: float = 0.0
    classification_accuracy: Optional[float] = None
    calculation_accuracy: Optional[float] = None
    false_auto_resolve: int = 0
    throughput_per_sec: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    duplicates_detected: int = 0
    audit_completeness: float = 0.0
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""

    def __post_init__(self):
        if not self.run_id:
            self.run_id = new_id("run")
        if not self.started_at:
            self.started_at = _now_iso()

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "source": self.source,
            "total_records": self.total_records,
            "total_cases": self.total_cases,
            "matched": self.matched,
            "review_required": self.review_required,
            "exceptions": self.exceptions,
            "match_rate": self.match_rate,
            "classification_accuracy": self.classification_accuracy,
            "calculation_accuracy": self.calculation_accuracy,
            "false_auto_resolve": self.false_auto_resolve,
            "throughput_per_sec": self.throughput_per_sec,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "duplicates_detected": self.duplicates_detected,
            "audit_completeness": self.audit_completeness,
            "errors": list(self.errors),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


def compute_record_key(tenant_id: str, source: str, external_id: str) -> str:
    """Idempotency key for a normalized record: (tenant, source, external_id)."""
    return f"{tenant_id}::{source}::{external_id}"