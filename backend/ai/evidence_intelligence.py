"""Evidence Intelligence — Gemini's role in the Finance Controller.

Gemini performs deep evidence analysis for complex/unstructured cases.
It is invoked ONLY when deterministic routing determines that deep
evidence interpretation is required.

Architecture:
  Groq (controller) → needs_deep_evidence_analysis() → Gemini (evidence)
  → structured evidence result → validate → compact result → Groq (continue)

Gemini MUST NOT:
  - Calculate monetary amounts
  - Determine final payout
  - Approve/reject payments
  - Introduce new policy IDs
  - Introduce new evidence IDs

Gemini returns ONLY:
  - Extracted facts with evidence references
  - Document interpretation
  - Contradiction identification
  - Confidence assessment
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from ai.llm_provider import get_provider, is_ai_available

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic routing — decides when Gemini is needed
# ---------------------------------------------------------------------------

# Evidence source types that may benefit from deep interpretation
_DEEP_ANALYSIS_SOURCE_TYPES = frozenset({
    "document",
    "uploaded_document",
    "contract",
    "invoice_image",
    "email_correspondence",
    "legal_notice",
})

# Evidence types with large unstructured content
_UNSTRUCTURED_TYPES = frozenset({
    "complaint",
    "email_correspondence",
    "legal_notice",
    "seller_response",
})

# Content length threshold (chars) — above this, evidence may be complex
_LONG_CONTENT_THRESHOLD = 2000


def needs_deep_evidence_analysis(
    evidence_records: list[dict],
    *,
    force_deep: bool = False,
) -> bool:
    """Determine whether this case requires Gemini deep evidence analysis.

    This function is DETERMINISTIC — no randomness, no LLM calls.
    The application decides when deep analysis is needed.

    Triggers for deep analysis:
      1. force_deep=True (manual override)
      2. Any evidence has a document/uploaded source type
      3. Multiple evidence records with conflicting source types
         (e.g., delivery says "on time" but complaint says "late")
      4. Evidence with long unstructured content (>2000 chars)
      5. More than 5 evidence records (complexity heuristic)

    Does NOT trigger for:
      - Simple Razorpay structured payment/order data
      - Small numbers of structured evidence records
      - Cases with only order + delivery + refund evidence
    """
    if force_deep:
        logger.info("Gemini routing: forced deep analysis")
        return True

    if not evidence_records:
        return False

    # Check for document-type evidence
    source_types = {ev.get("source_type", "") for ev in evidence_records}
    if source_types & _DEEP_ANALYSIS_SOURCE_TYPES:
        logger.info(
            "Gemini routing: document evidence detected (%s)",
            source_types & _DEEP_ANALYSIS_SOURCE_TYPES,
        )
        return True

    # Check for long unstructured content
    for ev in evidence_records:
        try:
            content = json.loads(ev.get("raw_content", "{}"))
            # Check raw_content length if it's a string
            raw = ev.get("raw_content", "")
            if isinstance(raw, str) and len(raw) > _LONG_CONTENT_THRESHOLD:
                if ev.get("source_type", "") in _UNSTRUCTURED_TYPES:
                    logger.info(
                        "Gemini routing: long unstructured content in %s (%d chars)",
                        ev.get("evidence_id"), len(raw),
                    )
                    return True
        except (json.JSONDecodeError, TypeError):
            continue

    # Complexity heuristic: many evidence records
    if len(evidence_records) > 5:
        logger.info(
            "Gemini routing: complexity heuristic (%d evidence records)",
            len(evidence_records),
        )
        return True

    # Check for potentially conflicting structured evidence
    # (e.g., delivery source type with different conclusions)
    if _has_potential_contradictions(evidence_records):
        logger.info("Gemini routing: potential contradictions detected")
        return True

    return False


def _has_potential_contradictions(evidence_records: list[dict]) -> bool:
    """Detect potential contradictions in structured evidence.

    This is a deterministic heuristic — no LLM involved.
    """
    # Group evidence by order_id
    by_order: dict[str, list[dict]] = {}
    for ev in evidence_records:
        try:
            content = json.loads(ev.get("raw_content", "{}"))
            oid = content.get("order_id", "")
            if oid:
                by_order.setdefault(oid, []).append(ev)
        except (json.JSONDecodeError, TypeError):
            continue

    # Check for delivery vs complaint contradictions
    for oid, evs in by_order.items():
        has_delivery = any(ev.get("source_type") == "delivery" for ev in evs)
        has_complaint = any(ev.get("source_type") == "complaint" for ev in evs)
        has_refund = any(ev.get("source_type") == "refund_record" for ev in evs)

        if has_complaint and has_delivery:
            # Check if delivery says on-time but complaint mentions delay
            for ev in evs:
                if ev.get("source_type") == "complaint":
                    try:
                        content = json.loads(ev.get("raw_content", "{}"))
                        issue = content.get("issue", "").lower()
                        if any(w in issue for w in ("late", "delay", "missing", "not delivered")):
                            return True
                    except (json.JSONDecodeError, TypeError):
                        continue

    return False


# ---------------------------------------------------------------------------
# Gemini evidence contract — structured result
# ---------------------------------------------------------------------------

@dataclass
class DeepEvidenceResult:
    """Structured result from Gemini's deep evidence analysis.

    This is the contract between Gemini and the controller.
    Gemini produces this; the controller validates it.
    """
    facts: list[dict] = field(default_factory=list)
    document_references: list[str] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    source_evidence_ids: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "facts": self.facts,
            "document_references": self.document_references,
            "contradictions": self.contradictions,
            "confidence": self.confidence,
            "source_evidence_ids": self.source_evidence_ids,
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Gemini evidence analysis — structured output schema
# ---------------------------------------------------------------------------

try:
    from pydantic import BaseModel, Field as PydanticField

    class GeminiEvidenceFact(BaseModel):
        """A single fact extracted by Gemini from complex evidence."""
        fact_type: str = PydanticField(description="Type of fact")
        value: str = PydanticField(description="Description of the fact")
        evidence_id: str = PydanticField(description="Source evidence ID")
        confidence: float = PydanticField(ge=0.0, le=1.0)

    class GeminiEvidenceResult(BaseModel):
        """Schema for Gemini's structured evidence analysis output."""
        facts: list[GeminiEvidenceFact]
        contradictions: list[str] = []
        confidence: float = PydanticField(ge=0.0, le=1.0)
        source_evidence_ids: list[str]
        summary: str

    _GEMINI_SCHEMA_AVAILABLE = True
except ImportError:
    _GEMINI_SCHEMA_AVAILABLE = False


# ---------------------------------------------------------------------------
# Invoke Gemini for deep evidence analysis
# ---------------------------------------------------------------------------

GEMINI_EVIDENCE_SYSTEM = (
    "You are a financial evidence intelligence system. "
    "Analyze complex or unstructured evidence documents and extract "
    "structured facts. You MUST NOT calculate monetary amounts, approve "
    "payments, or determine policy amounts. "
    "You extract WHAT happened, not how much money to move. "
    "Every fact must reference a specific evidence_id. "
    "Detect contradictions between evidence records. "
    "Return ONLY valid JSON."
)

GEMINI_EVIDENCE_PROMPT = """Analyze the following evidence records and extract structured facts.

Evidence records:
{evidence_text}

For each piece of evidence:
1. Extract key facts (what happened, when, who, what was said)
2. Reference the evidence_id
3. Rate your confidence (0.0-1.0)
4. Identify any contradictions between evidence records

Return a JSON object with:
{{
  "facts": [
    {{
      "fact_type": "description of fact type",
      "value": "what the evidence says",
      "evidence_id": "the evidence_id this comes from",
      "confidence": 0.0-1.0
    }}
  ],
  "contradictions": ["description of contradiction between evidence X and Y"],
  "confidence": 0.0-1.0,
  "source_evidence_ids": ["list of all evidence_ids analyzed"],
  "summary": "brief summary of evidence analysis"
}}

RULES:
- You MUST NOT calculate amounts or determine financial outcomes.
- Every fact MUST reference a real evidence_id from the provided records.
- If evidence is contradictory, say so — do NOT pick a winner.
- confidence reflects the overall quality/consistency of the evidence.
"""


async def analyze_evidence_with_gemini(
    evidence_records: list[dict],
    *,
    tenant_id: str = "",
) -> DeepEvidenceResult:
    """Invoke Gemini for deep evidence analysis.

    This is called ONLY when needs_deep_evidence_analysis() returns True.
    The result is validated before being returned to the controller.

    Args:
        evidence_records: Evidence records to analyze
        tenant_id: For logging (NOT sent to Gemini)

    Returns:
        DeepEvidenceResult with validated facts and contradictions
    """
    if not is_ai_available():
        logger.warning("Gemini evidence analysis requested but no provider available")
        return DeepEvidenceResult(
            facts=[],
            confidence=0.0,
            source_evidence_ids=[ev.get("evidence_id", "") for ev in evidence_records],
            summary="Gemini not available — evidence analysis skipped",
        )

    provider = get_provider()
    provider_name = provider.provider_info().get("provider", "unknown")

    # Only Gemini should be used for deep evidence analysis
    if provider_name != "gemini":
        logger.warning(
            "Deep evidence analysis requested but provider is %s (not gemini). "
            "Attempting Gemini via get_provider_by_name.",
            provider_name,
        )
        try:
            from ai.llm_provider import get_provider_by_name
            provider = get_provider_by_name("gemini")
        except (EnvironmentError, ValueError) as e:
            logger.error("Cannot get Gemini provider: %s", e)
            return DeepEvidenceResult(
                facts=[],
                confidence=0.0,
                source_evidence_ids=[ev.get("evidence_id", "") for ev in evidence_records],
                summary=f"Gemini provider not available: {e}",
            )

    # Format evidence for the prompt (bounded — only key fields)
    evidence_summaries = []
    for ev in evidence_records:
        try:
            content = json.loads(ev.get("raw_content", "{}"))
            summary = {
                "evidence_id": ev.get("evidence_id", ""),
                "source_type": ev.get("source_type", ""),
            }
            # Include relevant fields based on type
            for key in ("order_id", "amount", "status", "issue", "severity",
                        "promised_date", "actual_date", "delay_days",
                        "reason", "refund_amount", "resolution"):
                if key in content:
                    summary[key] = content[key]
            evidence_summaries.append(summary)
        except (json.JSONDecodeError, TypeError):
            evidence_summaries.append({
                "evidence_id": ev.get("evidence_id", ""),
                "source_type": ev.get("source_type", ""),
            })

    evidence_text = json.dumps(evidence_summaries, indent=2)
    prompt = GEMINI_EVIDENCE_PROMPT.format(evidence_text=evidence_text)

    logger.info(
        "Gemini evidence analysis: %d records, provider=%s",
        len(evidence_records), provider_name,
    )

    try:
        # Use complete_json with the evidence schema
        schema = GeminiEvidenceResult if _GEMINI_SCHEMA_AVAILABLE else None
        parsed = provider.complete_json(
            prompt,
            system=GEMINI_EVIDENCE_SYSTEM,
            max_tokens=2048,
            temperature=0.0,
            response_schema=schema,
        )

        # Validate the response
        result = _validate_gemini_evidence_result(parsed, evidence_records)
        return result

    except Exception as e:
        logger.error("Gemini evidence analysis failed: %s", str(e))
        # Return safe fallback — not a failure, just skipped
        return DeepEvidenceResult(
            facts=[],
            confidence=0.0,
            source_evidence_ids=[ev.get("evidence_id", "") for ev in evidence_records],
            summary=f"Gemini analysis failed: {str(e)[:200]}",
        )


def _validate_gemini_evidence_result(
    parsed: dict,
    evidence_records: list[dict],
) -> DeepEvidenceResult:
    """Validate Gemini's evidence analysis result.

    Checks:
    - All referenced evidence_ids exist in the provided evidence
    - Confidence is in valid range
    - Facts list is bounded
    - No financial amounts or approval language
    """
    if not isinstance(parsed, dict):
        logger.warning("Gemini result is not a dict: %s", type(parsed))
        return DeepEvidenceResult(
            summary="Invalid Gemini result format",
        )

    available_evidence_ids = {ev.get("evidence_id", "") for ev in evidence_records}

    # Validate evidence references
    source_ids = parsed.get("source_evidence_ids", [])
    valid_source_ids = [eid for eid in source_ids if eid in available_evidence_ids]
    invalid_source_ids = [eid for eid in source_ids if eid not in available_evidence_ids]

    if invalid_source_ids:
        logger.warning(
            "Gemini referenced non-existent evidence: %s",
            invalid_source_ids,
        )

    # Validate facts
    facts = parsed.get("facts", [])
    validated_facts = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        eid = fact.get("evidence_id", "")
        if eid not in available_evidence_ids:
            logger.warning(
                "Gemini fact references non-existent evidence: %s", eid,
            )
            continue
        # Bound facts list
        if len(validated_facts) >= 50:
            break
        validated_facts.append(fact)

    # Validate contradictions
    contradictions = parsed.get("contradictions", [])
    if not isinstance(contradictions, list):
        contradictions = []

    # Validate confidence
    confidence = parsed.get("confidence", 0.0)
    if not isinstance(confidence, (int, float)):
        confidence = 0.0
    confidence = max(0.0, min(1.0, float(confidence)))

    return DeepEvidenceResult(
        facts=validated_facts,
        document_references=valid_source_ids,
        contradictions=contradictions,
        confidence=confidence,
        source_evidence_ids=valid_source_ids,
        summary=parsed.get("summary", ""),
    )
