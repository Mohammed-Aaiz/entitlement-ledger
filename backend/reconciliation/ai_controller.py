"""AI interpretation controller for reconciliation.

The AI controller sits INSIDE the workflow but is strictly advisory:
- It interprets heterogeneous financial evidence.
- It identifies relationships between records.
- It explains discrepancies.
- It classifies ambiguity and contradictory evidence.

It NEVER:
- calculates or invents monetary values
- overrides deterministic settlement calculations
- silently converts an exception into an approval

Failure handling is first-class.  When the provider fails (429, 500, 503,
timeout, malformed output, quota exhaustion, missing key, tool-call
incompatibility) the controller records the exact technical reason and
returns a failure status.  The decision gate then proceeds
deterministically and escalates to REVIEW_REQUIRED when deterministic
evidence is insufficient.  AI failure NEVER produces an approval.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field

from .models import (
    AI_AVAILABLE,
    AI_UNAVAILABLE,
    AI_FAILED,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured AI output schema (advisory only)
# ---------------------------------------------------------------------------

class AIRelation(BaseModel):
    record_ref: str = Field(description="External id of the related record")
    relation: str = Field(description="e.g. 'refund_for_payment', 'settlement_for_payment'")


class AIContradiction(BaseModel):
    between: list[str] = Field(description="External ids of the contradicting records")
    description: str = Field(description="What contradicts")


class ReconciliationInterpretation(BaseModel):
    """Schema-validated AI interpretation of a reconciliation case.

    All monetary facts shown to the model are deterministic; the model is
    told these are authoritative and must not invent or change amounts.
    """
    evidence_summary: str = Field(description="Neutral summary of the evidence set")
    identified_relations: list[AIRelation] = Field(default_factory=list)
    discrepancy_explanation: str = Field(
        default="", description="Explain any discrepancy WITHOUT changing amounts"
    )
    contradictions: list[AIContradiction] = Field(default_factory=list)
    ambiguous: bool = Field(default=False, description="Whether the case is genuinely ambiguous")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    suggested_human_review: bool = Field(
        default=False, description="Whether the model believes a human should review"
    )


@dataclass
class AIInterpretationResult:
    """Outcome of an AI interpretation attempt."""

    status: str  # available | unavailable | failed
    interpretation: Optional[dict] = None
    technical_reason: str = ""
    latency_ms: int = 0
    provider: str = ""
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "interpretation": self.interpretation,
            "technical_reason": self.technical_reason,
            "latency_ms": self.latency_ms,
            "provider": self.provider,
            "model": self.model,
        }


def build_interpretation_prompt(case_context: dict) -> tuple[str, str]:
    """Build the system + user prompt for AI interpretation.

    The prompt includes ONLY deterministic facts (already-normalized
    records).  The model is explicitly forbidden from altering amounts.
    """
    system = (
        "You are the AI Controller inside a finance reconciliation system. "
        "The deterministic finance engine has already computed all monetary "
        "amounts — they are authoritative and MUST NOT be changed, invented, "
        "or re-calculated by you. "
        "Your job is limited to: interpreting evidence, identifying record "
        "relationships, explaining discrepancies, flagging contradictions, "
        "and classifying ambiguity. "
        "Never approve a transaction. Never convert an exception into an "
        "approval. Never fabricate missing evidence. "
        "If a case is genuinely ambiguous or evidence is missing, set "
        "ambiguous=true and suggested_human_review=true."
    )

    lines = [
        "Reconcile the following normalized financial records. All amounts are in paise (integer subunits).",
        json.dumps(case_context, indent=2, default=str),
    ]
    user = "\n".join(lines)
    return system, user


def _classify_failure(exc: Exception) -> str:
    """Classify a provider failure into a stable, safe technical reason.

    Never includes API keys or raw secrets.  Maps common provider errors
    to explicit codes so the pipeline can distinguish them.
    """
    msg = str(exc)
    lower = msg.lower()
    if "429" in msg or "rate limit" in lower:
        return "HTTP 429: provider rate limit / quota exceeded"
    if "503" in msg:
        return "HTTP 503: provider unavailable (service overloaded)"
    if "500" in msg or "internal server error" in lower:
        return "HTTP 500: provider internal error"
    if "timeout" in lower or "timed out" in lower or "deadline" in lower:
        return "provider timeout"
    if "api key" in lower or "api_key" in lower or "unauthorized" in lower or "401" in lower:
        return "provider authentication failed (missing/invalid API key)"
    if "connection" in lower or "resolve" in lower or "unreachable" in lower:
        return "provider unavailable (connection error)"
    if "tool" in lower and ("incompat" in lower or "invalid" in lower):
        return "tool-call incompatibility"
    if "json" in lower or "schema" in lower or "parse" in lower or "malformed" in lower:
        return "malformed model output"
    return f"provider error: {type(exc).__name__}"


def interpret_case(
    case_context: dict,
    provider=None,
    max_tokens: int = 1024,
) -> AIInterpretationResult:
    """Request schema-validated AI interpretation for a reconciliation case.

    Args:
        case_context: deterministic facts about the case (records, amounts,
            match notes).  Must NOT include ground truth.
        provider: LLM provider instance.  Defaults to the system provider.
            Injectable for tests (including failure simulation).

    Returns:
        AIInterpretationResult.  On ANY failure, status is 'unavailable'
        or 'failed' with the technical reason — never a fabricated
        interpretation.
    """
    start = time.time()

    if provider is None:
        try:
            from ai.llm_provider import get_provider
            provider = get_provider()
        except EnvironmentError as e:
            return AIInterpretationResult(
                status=AI_UNAVAILABLE,
                technical_reason=_classify_failure(e),
                latency_ms=int((time.time() - start) * 1000),
            )

    try:
        provider_info = provider.provider_info()
    except Exception:
        provider_info = {}

    system, user = build_interpretation_prompt(case_context)

    try:
        result = provider.complete_json(
            prompt=user,
            system=system,
            max_tokens=max_tokens,
            temperature=0.0,
            response_schema=ReconciliationInterpretation,
        )
    except Exception as e:
        # Provider failure (429/500/503/timeout/malformed/etc.) — first-class
        # controlled state, never converted into an approval.
        elapsed_ms = int((time.time() - start) * 1000)
        reason = _classify_failure(e)
        logger.warning("AI interpretation failed for case (%s) after %dms", reason, elapsed_ms)
        status = AI_FAILED if "malformed" in reason else AI_UNAVAILABLE
        return AIInterpretationResult(
            status=status,
            technical_reason=reason,
            latency_ms=elapsed_ms,
            provider=provider_info.get("provider", ""),
            model=provider_info.get("model", ""),
        )

    # ── Validate the output against the schema ──
    # Malformed output is treated as AI failure — never silently accepted.
    try:
        if isinstance(result, dict):
            parsed = ReconciliationInterpretation(**result)
        elif hasattr(result, "model_dump"):
            parsed = ReconciliationInterpretation(**result.model_dump())
        else:
            parsed = ReconciliationInterpretation(**_coerce_to_dict(result))
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        reason = f"malformed model output: {type(e).__name__}"
        logger.warning("AI interpretation schema validation failed: %s", reason)
        return AIInterpretationResult(
            status=AI_FAILED,
            technical_reason=reason,
            latency_ms=elapsed_ms,
            provider=provider_info.get("provider", ""),
            model=provider_info.get("model", ""),
        )

    elapsed_ms = int((time.time() - start) * 1000)
    return AIInterpretationResult(
        status=AI_AVAILABLE,
        interpretation=parsed.model_dump(),
        latency_ms=elapsed_ms,
        provider=provider_info.get("provider", ""),
        model=provider_info.get("model", ""),
    )


def _coerce_to_dict(value) -> dict:
    """Best-effort coercion of provider output into a dict.

    Raises on failure — callers treat that as malformed output.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    raise ValueError(f"cannot coerce {type(value).__name__} to dict")