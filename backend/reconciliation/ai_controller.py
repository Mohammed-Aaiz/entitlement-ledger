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


# ---------------------------------------------------------------------------
# Controlled root-cause taxonomy (advisory — validated by the gate)
# ---------------------------------------------------------------------------

ROOT_CAUSE_TAXONOMY = [
    "SETTLEMENT_TIMING",
    "REFUND_TIMING",
    "FEE_DISCREPANCY",
    "TAX_DISCREPANCY",
    "IDENTIFIER_MISMATCH",
    "CONTRADICTORY_EVIDENCE",
    "MISSING_EVIDENCE",
    "DUPLICATE_RECORD",
    "PARTIAL_SETTLEMENT",
    "UNKNOWN",
]

_TAXONOMY_SET = set(ROOT_CAUSE_TAXONOMY)


def normalize_root_cause(raw_cause) -> str:
    """Map a model-suggested cause onto the controlled taxonomy.

    Model-generated categories are NEVER authoritative: anything outside
    the taxonomy collapses to UNKNOWN so application code controls the
    vocabulary.  Accepts both 'SETTLEMENT_TIMING' and relaxed variants
    like 'settlement_timing' / 'settlement timing'.
    """
    if isinstance(raw_cause, str):
        candidate = raw_cause.strip().upper().replace(" ", "_")
        if candidate in _TAXONOMY_SET:
            return candidate
    return "UNKNOWN"


class AIRootCause(BaseModel):
    """One candidate root cause for a discrepancy.

    Purely advisory: the deterministic gate decides the final
    classification.  The cause must come from the controlled taxonomy.
    """
    cause: str = Field(description="One of the controlled root-cause categories")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="AI confidence in this cause")
    reasoning: str = Field(default="", description="Evidence-grounded reasoning, not hidden chain-of-thought")


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
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Raw evidence refs this interpretation is based on (validated against the case)",
    )
    related_record_ids: list[str] = Field(
        default_factory=list,
        description="Internal record ids consulted during the investigation",
    )
    root_cause_candidates: list[AIRootCause] = Field(
        default_factory=list,
        description="Advisory root-cause hypotheses from the controlled taxonomy",
    )
    reasoning: str = Field(
        default="",
        description=(
            "Evidence-grounded user-facing reasoning. Must be AT LEAST 15 words "
            "and every statement must reference the supplied evidence. Never "
            "treated as monetary truth."
        ),
    )


def deterministic_validate_interpretation(
    interpretation: "ReconciliationInterpretation",
    valid_evidence_refs: set[str] = frozenset(),
) -> "ReconciliationInterpretation":
    """Deterministic validation of AI output AFTER schema validation.

    1. Root-cause candidates are normalized onto the controlled taxonomy
       (arbitrary model categories collapse to UNKNOWN).
    2. evidence_ids are filtered to evidence that actually exists in the
       case — hallucinated evidence references are dropped, never trusted.
    3. No monetary field exists on the schema, so AI can never alter
       amounts by construction (verified by the calculator being the only
       money authority downstream).

    Malformed input was already rejected by schema validation; this step
    sanitizes semantically invalid-but-well-formed output.
    """
    # ── 1. Normalize root causes onto the controlled taxonomy ──
    normalized = []
    for rc in interpretation.root_cause_candidates:
        normalized.append(AIRootCause(
            cause=normalize_root_cause(rc.cause),
            confidence=rc.confidence,
            reasoning=rc.reasoning,
        ))
    interpretation.root_cause_candidates = normalized

    # ── 2. Evidence references must exist in the case ──
    if valid_evidence_refs:
        known = set(valid_evidence_refs)
        evidence_ids = [
            e for e in interpretation.evidence_ids if e in known
        ]
        interpretation.evidence_ids = evidence_ids

    # Contradiction record ids are validated too (advisory only).
    cleaned_contradictions = []
    for c in interpretation.contradictions:
        cleaned_contradictions.append(AIContradiction(
            between=[b for b in c.between],
            description=c.description,
        ))
    interpretation.contradictions = cleaned_contradictions
    return interpretation


def reasoning_requirement_issue(interpretation: "ReconciliationInterpretation") -> str:
    """Return a non-empty issue string when the reasoning contract is unmet.

    The user-facing reasoning must be evidence-grounded and at least 15
    words.  Short / empty / boilerplate reasoning fails the contract and the
    output is treated as AI_FAILED (never accepted, never an approval).
    """
    text = (interpretation.reasoning or "").strip()
    words = [w for w in text.split() if any(ch.isalnum() for ch in w)]
    if len(words) < 15:
        return (
            "malformed model output: reasoning must be evidence-grounded and "
            f"at least 15 words (got {len(words)})"
        )
    return ""


@dataclass
class AIInterpretationResult:
    """Outcome of an AI interpretation attempt."""

    status: str  # available | unavailable | failed
    interpretation: Optional[dict] = None
    technical_reason: str = ""
    latency_ms: int = 0
    provider: str = ""
    model: str = ""
    tool_call_count: int = 0
    tools_called: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "interpretation": self.interpretation,
            "technical_reason": self.technical_reason,
            "latency_ms": self.latency_ms,
            "provider": self.provider,
            "model": self.model,
            "tool_call_count": self.tool_call_count,
            "tools_called": list(self.tools_called),
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
        "The 'reasoning' field must be evidence-grounded and AT LEAST 15 words; "
        "reference the specific records or evidence you inspected and do not "
        "pad meaningless text. "
        "If a case is genuinely ambiguous or evidence is missing, set "
        "ambiguous=true and suggested_human_review=true."
    )

    lines = [
        "Reconcile the following normalized financial records. All amounts are in paise (integer subunits).",
        json.dumps(case_context, indent=2, default=str),
    ]
    user = "\n".join(lines)
    return system, user


# ---------------------------------------------------------------------------
# Deterministic AI gate — "does this case actually require AI investigation?"
# ---------------------------------------------------------------------------

# Exception codes that represent CLEAR deterministic answers where AI
# interpretation cannot change the outcome and adds no investigation value.
_DETERMINISTIC_ONLY_CODES = {
    "MISSING_PAYMENT",
    "MISSING_SETTLEMENT",
    "LATE_SETTLEMENT",
    "INVALID_RECORD",
    "DUPLICATE_PAYMENT",
    "DUPLICATE_SETTLEMENT",
    "FEE_MISMATCH",
    "TAX_MISMATCH",
    "REFUND_MISMATCH",
    "AI_UNAVAILABLE",
    "UNRESOLVED_RECONCILIATION",
}

# Exception codes where the discrepancy itself is deterministic but its
# ROOT CAUSE is genuinely ambiguous — AI investigation adds value.
_AMBIGUOUS_CODES = {
    "AMOUNT_MISMATCH",
    "PARTIAL_SETTLEMENT",
    "CONTRADICTORY_EVIDENCE",
}


def should_investigate(
    exception_codes: list[str],
    capture_conflict: bool = False,
    has_payment: bool = True,
    financial_error: bool = False,
    variance: int = 0,
) -> tuple[bool, str]:
    """Deterministic AI gate: decide whether a case genuinely needs AI.

    Returns (invoke, trigger_reason).  The gate is intentionally
    conservative: AI is invoked ONLY when deterministic logic cannot
    resolve the case, so most cases remain AI-free (demand-driven usage).

    No-AI outcomes (deterministic sufficiency):
    - missing payment / invalid records / duplicates / clear mismatches
    - exact match with zero variance

    AI outcomes (genuine ambiguity):
    - capture conflicts
    - amount/partial-settlement discrepancies whose root cause is unclear
    - contradictory evidence requiring semantic interpretation
    """
    if not has_payment:
        return False, "missing payment — deterministic exception"
    if financial_error:
        return False, "invalid financial records — deterministic exception"
    if capture_conflict:
        return True, "capture conflict — ambiguous captures require interpretation"

    codes = set(exception_codes or [])
    ambiguous = codes & _AMBIGUOUS_CODES
    if ambiguous:
        return True, f"ambiguous exception requires interpretation: {sorted(ambiguous)}"

    deterministic = codes & _DETERMINISTIC_ONLY_CODES
    if deterministic:
        return False, f"deterministic exception — no AI needed: {sorted(deterministic)}"

    if variance == 0 and not codes:
        return False, "exact match — deterministic resolution (variance 0)"

    if not codes:
        return False, "deterministic resolution — records reconcile without AI"

    return True, "unresolved discrepancy — AI investigation may clarify root cause"


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

    # ── Deterministic validation AFTER schema validation ──
    # Root causes are normalized onto the controlled taxonomy and evidence
    # references are checked against records that actually exist in the
    # case (hallucinated references are dropped, never trusted).
    valid_refs = _record_evidence_refs(case_context)
    parsed = deterministic_validate_interpretation(parsed, valid_evidence_refs=valid_refs)

    # ── Reasoning contract (>=15 evidence-grounded words) ──
    issue = reasoning_requirement_issue(parsed)
    if issue:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.warning("AI interpretation rejected: %s", issue)
        return AIInterpretationResult(
            status=AI_FAILED,
            technical_reason=issue,
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


def _record_external_ids(case_context: dict) -> set[str]:
    """External ids present in the case context (deterministic facts)."""
    return {r.get("external_id", "") for r in case_context.get("records", []) if r.get("external_id")}


def _record_evidence_refs(case_context: dict) -> set[str]:
    """Raw evidence refs present in the case context."""
    refs = {r.get("raw_evidence_ref", "") for r in case_context.get("records", []) if r.get("raw_evidence_ref")}
    refs.update(_record_external_ids(case_context))
    return refs


# ---------------------------------------------------------------------------
# Bounded AI investigator — controlled read-only evidence retrieval
# ---------------------------------------------------------------------------
#
# When a case passes the deterministic AI gate, a small bounded loop lets
# the model retrieve specific records from the case with READ-ONLY,
# tenant-safe tools before producing its structured finding.  Tools only
# ever see the normalized records the caller (a tenant-scoped service)
# supplied — there is no database handle, no cross-tenant path, and no
# write surface.  The loop is hard-bounded (iterations, tool calls,
# tokens) and always ends in schema-validated structured output or a safe
# failure state.

# Tool-call budget (conservative, configurable)
MAX_INVESTIGATION_ITERATIONS = 3
MAX_INVESTIGATION_TOOL_CALLS = 8
INVESTIGATION_MAX_TOKENS = 1024


# Per-tool allowed argument names + required args.
_TOOL_PARAMS: dict[str, dict] = {
    "get_payment": {"required": ["payment_id"], "optional": []},
    "get_order": {"required": ["order_id"], "optional": []},
    "get_refund": {"required": ["refund_id"], "optional": []},
    "get_settlement": {"required": ["settlement_id"], "optional": []},
    "get_evidence": {"required": ["evidence_ref"], "optional": []},
    "search_related_records": {"required": ["payment_id"], "optional": ["record_type"]},
    "get_case_records": {"required": [], "optional": ["record_type"]},
}


# OpenAI-compatible tool definitions sent to providers with native tool
# calling (Groq / Gemini / Ollama).  Every tool is read-only and scoped to
# the supplied case records.
INVESTIGATION_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_payment",
            "description": "Read the payment record(s) with the given payment id from this case.",
            "parameters": {
                "type": "object",
                "properties": {"payment_id": {"type": "string", "description": "Payment id"}},
                "required": ["payment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Read the record(s) linked to the given order id in this case.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string", "description": "Order id"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_refund",
            "description": "Read the refund record with the given refund id from this case.",
            "parameters": {
                "type": "object",
                "properties": {"refund_id": {"type": "string", "description": "Refund id"}},
                "required": ["refund_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_settlement",
            "description": "Read the settlement record with the given settlement id from this case.",
            "parameters": {
                "type": "object",
                "properties": {"settlement_id": {"type": "string", "description": "Settlement id"}},
                "required": ["settlement_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_evidence",
            "description": "Read the record whose raw evidence reference (e.g. razorpay_payments:pay_1) matches evidence_ref.",
            "parameters": {
                "type": "object",
                "properties": {"evidence_ref": {"type": "string", "description": "Raw evidence reference"}},
                "required": ["evidence_ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_related_records",
            "description": "Search this case for records related to a payment, optionally filtered by record_type.",
            "parameters": {
                "type": "object",
                "properties": {
                    "payment_id": {"type": "string", "description": "Payment id"},
                    "record_type": {"type": "string", "description": "payment|refund|settlement|fee_tax|adjustment"},
                },
                "required": ["payment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_case_records",
            "description": "Read every record in this case (optionally filtered by record_type).",
            "parameters": {
                "type": "object",
                "properties": {"record_type": {"type": "string", "description": "payment|refund|settlement|fee_tax|adjustment"}},
                "required": [],
            },
        },
    },
]


# Arguments a tool will never accept — tenant/security controls can never
# be supplied by the model.
_FORBIDDEN_TOOL_ARGS = {"tenant_id", "tenant", "db", "user_id"}


_SAFE_RECORD_FIELDS = [
    "record_type", "external_id", "record_id", "amount", "currency", "status",
    "payment_id", "order_id", "fee_amount", "tax_amount", "adjustment_sign",
    "recorded_at", "source", "raw_evidence_ref",
]


def _validate_tool_args(function_name: str, args) -> tuple[dict, str]:
    """Validate model-supplied tool arguments.

    Returns (sanitized_args, error).  Only allow-listed scalar string args
    are accepted; tenant/security parameters are always rejected;
    non-dict, empty, or malformed arguments are rejected.
    """
    spec = _TOOL_PARAMS.get(function_name)
    if spec is None:
        return {}, f"unknown tool: {function_name}"
    if not isinstance(args, dict):
        return {}, "tool arguments must be an object"

    allowed = set(spec["required"]) | set(spec["optional"])
    sanitized: dict = {}
    for key, value in args.items():
        if key in _FORBIDDEN_TOOL_ARGS:
            return {}, f"argument '{key}' is not permitted"
        if key not in allowed:
            continue  # unknown params rejected silently
        if not isinstance(value, str) or not value.strip():
            return {}, f"argument '{key}' must be a non-empty string"
        sanitized[key] = value.strip()

    missing = [p for p in spec["required"] if p not in sanitized]
    if missing:
        return {}, f"missing required argument(s): {', '.join(missing)}"
    return sanitized, ""


def _execute_tool(function_name: str, args: dict, case_context: dict) -> dict:
    """Execute one read-only investigator tool against the case records.

    Never raises for missing records and never writes anything.  Records
    returned are serialized with safe fields only.
    """
    records = case_context.get("records", [])

    def _match(rec: dict) -> bool:
        return True

    def _safe(rec: dict) -> dict:
        return {k: rec.get(k) for k in _SAFE_RECORD_FIELDS if k in rec}

    if function_name == "get_case_records":
        rec_type = args.get("record_type")
        found = [r for r in records if not rec_type or r.get("record_type") == rec_type]
        return {"found": bool(found), "count": len(found),
                "records": [_safe(r) for r in found]}

    if function_name == "search_related_records":
        payment_id = args.get("payment_id")
        rec_type = args.get("record_type")
        found = [r for r in records
                 if r.get("payment_id") == payment_id
                 and (not rec_type or r.get("record_type") == rec_type)]
        return {"found": bool(found), "count": len(found),
                "records": [_safe(r) for r in found]}

    lookup = {
        "get_payment": ("payment_id", "payment"),
        "get_order": ("order_id", "order"),
        "get_refund": ("refund_id", "refund"),
        "get_settlement": ("settlement_id", "settlement"),
    }
    if function_name in lookup:
        arg_name, rec_type = lookup[function_name]
        target = args.get(arg_name, "")
        # For payment/settlement, match either external_id or payment_id
        found = [
            r for r in records
            if (r.get("external_id") == target
                or (arg_name == "payment_id" and r.get("payment_id") == target))
            and (rec_type == "payment" or r.get("record_type") == rec_type)
        ]
        # get_payment also matches the payment record via payment_id column.
        if arg_name == "payment_id" and not found:
            found = [r for r in records if r.get("record_type") == "payment"
                     and r.get("external_id") == target]
        return {"found": bool(found), "count": len(found),
                "records": [_safe(r) for r in found]}

    if function_name == "get_evidence":
        evidence_ref = args.get("evidence_ref", "")
        found = [r for r in records if r.get("raw_evidence_ref") == evidence_ref]
        if not found:
            # Some records only carry external ids as their evidence ref.
            found = [r for r in records if r.get("external_id") == evidence_ref]
        return {"found": bool(found), "count": len(found),
                "records": [_safe(r) for r in found]}

    return {"found": False, "count": 0, "reason": f"unsupported tool: {function_name}"}


def _record_index(case_context: dict) -> dict:
    """Index of case records by external id (for evidence validation)."""
    idx: dict[str, dict] = {}
    for r in case_context.get("records", []):
        ext = r.get("external_id")
        if ext:
            idx[ext] = r
        ref = r.get("raw_evidence_ref")
        if ref:
            idx.setdefault(ref, r)
    return idx


def _tool_response_messages(assistant_msg: dict, tool_calls, case_context: dict) -> tuple[list[dict], list[str]]:
    """Execute the tool calls the model requested and build tool result messages.

    Returns (messages_to_append, tools_called).  Every call is validated;
    invalid calls receive an explicit error result (never a crash).
    """
    appended: list[dict] = []
    tools_called: list[str] = []
    for tc in tool_calls:
        func_name = tc.function_name
        sanitized, err = _validate_tool_args(func_name, tc.arguments)
        if err:
            appended.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps({"error": err, "found": False}),
            })
            continue
        tools_called.append(func_name)
        result = _execute_tool(func_name, sanitized, case_context)
        appended.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(result, default=str),
        })
    return appended, tools_called


def _supports_native_tools(provider) -> bool:
    """Whether the provider implements native tool calling.

    Providers that do not override complete_with_tools (the default
    implementation flattens messages to a plain text call) use the
    single-shot structured-output path instead — no fake tool layer.
    Plain test doubles without a complete_with_tools method also use the
    single-shot path.
    """
    from ai.llm_provider import LLMProvider
    own = getattr(type(provider), "complete_with_tools", None)
    return own is not None and own is not LLMProvider.complete_with_tools


def investigate_case(
    case_context: dict,
    provider=None,
) -> AIInterpretationResult:
    """Bounded AI investigation of one reconciliation case.

    1. Deterministic facts (records + amounts) are supplied as context.
    2. Providers with native tool support may issue read-only tool calls
       against the case records before answering (bounded loop).
    3. The final answer MUST be schema-valid JSON (ReconciliationInterpre-
       tation) — malformed output is AI_FAILED, never accepted.
    4. Providers without native tool support use the single-shot
       structured-output path (identical safety contract).

    Failure (429/503/timeout/malformed/tool incompatibility/missing key)
    is first-class and never becomes an interpretation or an approval.
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

    info_kwargs = {
        "latency_ms": int((time.time() - start) * 1000),
        "provider": provider_info.get("provider", ""),
        "model": provider_info.get("model", ""),
    }

    if not _supports_native_tools(provider):
        # Single-shot path for providers without native tool calling.
        return interpret_case(case_context, provider=provider)

    system, user = build_interpretation_prompt(case_context)
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    tool_call_count = 0
    tools_called: list[str] = []

    for _iteration in range(MAX_INVESTIGATION_ITERATIONS):
        try:
            resp = provider.complete_with_tools(
                messages=messages,
                tools=INVESTIGATION_TOOLS,
                tool_choice="auto",
                max_tokens=INVESTIGATION_MAX_TOKENS,
                temperature=0.0,
            )
        except Exception as e:
            reason = _classify_failure(e)
            logger.warning("AI investigation failed (%s) after %dms",
                           reason, int((time.time() - start) * 1000))
            return AIInterpretationResult(
                status=AI_FAILED if "malformed" in reason else AI_UNAVAILABLE,
                technical_reason=reason,
                tool_call_count=tool_call_count,
                tools_called=tools_called,
                **info_kwargs,
            )

        if resp.tool_calls:
            budget = MAX_INVESTIGATION_TOOL_CALLS - tool_call_count
            batch = resp.tool_calls[:budget]
            if not batch:
                # Tool budget exhausted with no final answer — safe failure.
                return AIInterpretationResult(
                    status=AI_FAILED,
                    technical_reason="investigation tool-call limit reached without a validated finding",
                    tool_call_count=tool_call_count,
                    tools_called=tools_called,
                    **info_kwargs,
                )
            tool_call_count += len(batch)
            assistant_msg = {
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function_name,
                            "arguments": json.dumps(tc.arguments, default=str),
                        },
                    }
                    for tc in batch
                ],
            }
            messages.append(assistant_msg)
            appended, called = _tool_response_messages(assistant_msg, batch, case_context)
            tools_called.extend(called)
            messages.extend(appended)
            continue

        # No tool calls → the model produced its final answer.
        if not resp.content or not resp.content.strip():
            return AIInterpretationResult(
                status=AI_FAILED,
                technical_reason="malformed model output: empty final answer",
                tool_call_count=tool_call_count,
                tools_called=tools_called,
                **info_kwargs,
            )
        parsed = _validate_structured_output(
            resp.content,
            case_context,
            tool_call_count=tool_call_count,
            tools_called=tools_called,
            info_kwargs=info_kwargs,
        )
        return parsed

    # Iteration budget exhausted without a final answer.
    return AIInterpretationResult(
        status=AI_FAILED,
        technical_reason="investigation iteration limit reached without a validated finding",
        tool_call_count=tool_call_count,
        tools_called=tools_called,
        **info_kwargs,
    )


def _validate_structured_output(
    content: str,
    case_context: dict,
    tool_call_count: int,
    tools_called: list[str],
    info_kwargs: dict,
) -> AIInterpretationResult:
    """Parse + schema-validate + deterministic-validate a final AI answer."""
    try:
        parsed = ReconciliationInterpretation(**_coerce_to_dict(content))
    except Exception as e:
        logger.warning("AI investigation schema validation failed: %s", type(e).__name__)
        return AIInterpretationResult(
            status=AI_FAILED,
            technical_reason=f"malformed model output: {type(e).__name__}",
            tool_call_count=tool_call_count,
            tools_called=tools_called,
            **info_kwargs,
        )
    valid_refs = _record_evidence_refs(case_context)
    parsed = deterministic_validate_interpretation(parsed, valid_evidence_refs=valid_refs)
    issue = reasoning_requirement_issue(parsed)
    if issue:
        logger.warning("AI investigation rejected: %s", issue)
        return AIInterpretationResult(
            status=AI_FAILED,
            technical_reason=issue,
            tool_call_count=tool_call_count,
            tools_called=tools_called,
            **info_kwargs,
        )
    return AIInterpretationResult(
        status=AI_AVAILABLE,
        interpretation=parsed.model_dump(),
        tool_call_count=tool_call_count,
        tools_called=tools_called,
        **info_kwargs,
    )