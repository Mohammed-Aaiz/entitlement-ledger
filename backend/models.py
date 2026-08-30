"""Pydantic models for API requests and responses."""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional
import uuid


class LineItem(BaseModel):
    label: str
    amount: int
    type: str = Field(..., pattern=r"^(fee|deduction|credit)$")
    policy_clause_id: Optional[str] = None
    evidence_ids: list[str] = []


class AIExtractedFacts(BaseModel):
    claims: list[dict]


class DecisionCreate(BaseModel):
    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    entity_type: str
    entity_id: str
    gross_amount: int
    line_items: list[LineItem]
    final_amount: int
    policy_version_id: str
    approver_id: str
    approved_at: Optional[str] = None
    model_output: dict = {}
    prev_decision_hash: str = "genesis"
    decision_hash: str = ""
    created_at: str = ""


class DecisionResponse(BaseModel):
    decision_id: str
    entity_type: str
    entity_id: str
    gross_amount: int
    line_items: list[LineItem]
    final_amount: int
    policy_version_id: str
    approver_id: str
    approved_at: Optional[str] = None
    model_output: dict
    prev_decision_hash: str
    decision_hash: str
    created_at: str
    status: str = "APPROVED"  # DRAFT, REVIEW_REQUIRED, APPROVED, REJECTED


class EvidenceResponse(BaseModel):
    evidence_id: str
    source_type: str
    raw_content: str
    extracted_facts: list[dict]
    linked_decision_ids: list[str]


class PolicyResponse(BaseModel):
    policy_id: str
    version: str
    clause_text: str
    effective_date: str


class VerificationResult(BaseModel):
    valid: bool
    checked_count: int
    break_at: Optional[str] = None


class ScenarioResponse(BaseModel):
    scenario_id: str
    name: str
    description: str
    status: str


class DefensePacket(BaseModel):
    decision: DecisionResponse
    financial_breakdown: dict
    evidence: list[EvidenceResponse]
    policies: list[PolicyResponse]
    approver_id: str
    approved_at: Optional[str] = None
    integrity: VerificationResult


class RazorpayEventResponse(BaseModel):
    event_id: str
    event_type: str
    razorpay_entity_type: str
    razorpay_entity_id: str
    payment_id: str
    order_id: str
    amount: Optional[int] = None
    currency: str
    status: str
    event_timestamp: Optional[str] = None
    received_at: str
    extracted_facts: list[dict]
    linked_decision_id: Optional[str] = None


class RazorpayConnectionInfo(BaseModel):
    configured: bool
    key_id_present: bool
    key_id_preview: Optional[str] = None
    webhook_secret_present: bool
    mode: str
