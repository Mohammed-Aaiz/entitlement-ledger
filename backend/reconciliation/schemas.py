"""Pydantic schemas for the reconciliation API."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RecordInput(BaseModel):
    """One normalized record submitted for reconciliation."""

    record_type: str = Field(..., description="payment | refund | settlement | fee_tax | adjustment")
    external_id: str = Field(..., description="payment_id / refund_id / settlement_id / reference")
    # Negative amounts are intentionally allowed through the schema so the
    # service normalizer can reject them as INVALID_RECORD exceptions
    # (they must surface in the exception queue, not as a 422).
    amount: int = Field(..., description="Amount in integer paise (validated by the service)")
    currency: str = "INR"
    status: str = "unknown"
    payment_id: str = ""
    order_id: str = ""
    fee_amount: int = 0
    tax_amount: int = 0
    adjustment_sign: str = ""
    recorded_at: Optional[str] = None
    source: str = ""
    raw_evidence_ref: str = ""
    payload_hash: str = ""
    extra: dict = Field(default_factory=dict)


class ReconciliationRunRequest(BaseModel):
    """Request body for a batch reconciliation run."""

    records: list[RecordInput] = Field(default_factory=list)
    use_ai: bool = False
    source: str = "batch"


class ExceptionSummary(BaseModel):
    code: str
    explanation: str
    involved_record_ids: list[str] = Field(default_factory=list)
    financial_impact: int = 0
    evidence_refs: list[str] = Field(default_factory=list)
    human_action_required: bool = True


class ReconciliationCaseResponse(BaseModel):
    case_id: str
    payment_id: str
    run_id: str
    classification: str
    expected_amount: int
    actual_amount: int
    variance: int
    exception_codes: list[str] = Field(default_factory=list)
    exceptions: list[dict] = Field(default_factory=list)
    ai_status: str
    ai_confidence: Optional[float] = None
    ai_interpretation: dict = Field(default_factory=dict)
    ai_technical_reason: str = ""
    calculation_trace: dict = Field(default_factory=dict)
    match_info: dict = Field(default_factory=dict)
    decision_id: str = ""
    explanation: str = ""
    related_record_ids: list[str] = Field(default_factory=list)
    created_at: str = ""


class ReconciliationRunResponse(BaseModel):
    run_id: str
    status: str
    source: str
    total_records: int
    total_cases: int
    matched: int
    review_required: int
    exceptions: int
    match_rate: float
    classification_accuracy: Optional[float] = None
    calculation_accuracy: Optional[float] = None
    false_auto_resolve: int
    throughput_per_sec: float
    p50_latency_ms: float
    p95_latency_ms: float
    duplicates_detected: int
    audit_completeness: float
    errors: list[str] = Field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""


class ReconciliationDashboard(BaseModel):
    total_runs: int
    latest_run: Optional[dict] = None
    total_cases: int
    matched: int
    review_required: int
    exceptions: int
    match_rate: float
    total_variance: int
    unresolved_exceptions: list[dict] = Field(default_factory=list)
    false_auto_resolve_risk_cases: list[dict] = Field(default_factory=list)
    ledger_verified: bool = True