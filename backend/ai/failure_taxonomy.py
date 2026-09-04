"""Structured failure taxonomy for the Finance Controller Agent.

Every failure in the system maps to exactly one of these machine-readable
categories.  The taxonomy is used in:
  - AgentRunState.stop_reason
  - Pipeline exceptions
  - Decision model_output metadata
  - API responses (safe subset only)
  - Audit log entries

Categories that CAN result in REVIEW_REQUIRED (fail-closed):
  ALL of them.

Categories that CAN result in APPROVED:
  NONE.
"""
import enum


class FailureType(str, enum.Enum):
    """Machine-readable failure categories."""

    # Provider-level failures
    PROVIDER_ERROR = "provider_error"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"

    # Tool-level failures
    TOOL_ERROR = "tool_error"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"

    # Output validation failures
    SCHEMA_ERROR = "schema_error"
    SEMANTIC_VALIDATION_ERROR = "semantic_validation_error"

    # Reference validation failures
    INVALID_EVIDENCE_REFERENCE = "invalid_evidence_reference"
    INVALID_POLICY_REFERENCE = "invalid_policy_reference"

    # Evidence/claim contradictions
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"

    # Agent limits
    AGENT_LIMIT = "agent_limit"

    # Catch-all
    UNKNOWN = "unknown"


# ── Mapping helpers ──────────────────────────────────────────────────

# Map agent stop_reason values to FailureType
_STOP_REASON_MAP: dict[str, FailureType] = {
    "llm_error": FailureType.PROVIDER_ERROR,
    "provider_error": FailureType.PROVIDER_ERROR,
    "rate_limit": FailureType.RATE_LIMIT,
    "timeout": FailureType.TIMEOUT,
    "max_duration": FailureType.TIMEOUT,
    "max_tool_calls": FailureType.AGENT_LIMIT,
    "max_iterations": FailureType.AGENT_LIMIT,
    "tool_error": FailureType.TOOL_ERROR,
    "schema_error": FailureType.SCHEMA_ERROR,
    "invalid_tool_arguments": FailureType.INVALID_TOOL_ARGUMENTS,
    "evidence_gathering_complete": FailureType.UNKNOWN,  # not a failure
    "analysis_complete": FailureType.UNKNOWN,  # not a failure
}


def classify_provider_error(error_msg: str) -> FailureType:
    """Classify a provider error message into a FailureType."""
    lower = error_msg.lower()
    if "429" in lower or "rate" in lower and "limit" in lower:
        return FailureType.RATE_LIMIT
    if "timeout" in lower or "timed out" in lower:
        return FailureType.TIMEOUT
    if "api key" in lower or "unauthorized" in lower or "401" in lower:
        return FailureType.PROVIDER_ERROR
    return FailureType.PROVIDER_ERROR


def classify_stop_reason(stop_reason: str) -> FailureType:
    """Map an agent stop_reason to a FailureType.

    Returns FailureType.UNKNOWN for non-failure stop reasons
    (evidence_gathering_complete, analysis_complete).
    """
    return _STOP_REASON_MAP.get(stop_reason, FailureType.UNKNOWN)


def is_failure(stop_reason: str) -> bool:
    """Return True if the stop_reason represents an actual failure."""
    ft = classify_stop_reason(stop_reason)
    return ft != FailureType.UNKNOWN


def is_success(stop_reason: str) -> bool:
    """Return True if the agent completed successfully."""
    return stop_reason in ("evidence_gathering_complete", "analysis_complete")


# ── Safe subset for API responses ────────────────────────────────────
# We expose only these to the API response.  Internal details stay
# in the audit trail.

SAFE_API_FAILURE_TYPES = {
    FailureType.PROVIDER_ERROR,
    FailureType.RATE_LIMIT,
    FailureType.TIMEOUT,
    FailureType.CONTRADICTORY_EVIDENCE,
    FailureType.INSUFFICIENT_EVIDENCE,
    FailureType.AGENT_LIMIT,
    FailureType.UNKNOWN,
}


def safe_failure_type_for_api(failure_type: FailureType) -> str:
    """Return the failure type string if safe for API, else 'unknown'."""
    if failure_type in SAFE_API_FAILURE_TYPES:
        return failure_type.value
    return FailureType.UNKNOWN.value
