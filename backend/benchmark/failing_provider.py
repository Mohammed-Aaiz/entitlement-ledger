"""Provider-failure simulation for the reconciliation benchmark.

These stubs raise the exact failure classes the real providers can hit
(HTTP 429, 503, timeout, malformed output, quota exhaustion, tool-call
incompatibility).  The benchmark uses them to PROVE that AI failure never
produces an unsafe approval.  Results from these runs are explicitly
labeled "provider failure benchmark" — never "real AI accuracy".
"""
from __future__ import annotations

import json

from ai.llm_provider import LLMProvider


class _BaseFailingProvider(LLMProvider):
    provider_name = "failing_stub"

    def __init__(self) -> None:
        # Instrumentation for the provider-failure benchmark: counts how
        # many times this stub's AI endpoint was actually invoked, so the
        # benchmark can prove it genuinely exercised the failing provider
        # instead of silently running deterministic-only.
        self.call_count = 0

    def complete_json(self, *args, **kwargs):
        """Count the invocation, then fail exactly like the real path.

        The reconciliation controller calls complete_json() → complete().
        Counting here (one shared point instead of six overrides) records
        every genuine provider attempt.
        """
        self.call_count += 1
        return super().complete_json(*args, **kwargs)

    def is_available(self) -> bool:
        return True

    def provider_info(self) -> dict:
        return {
            "provider": self.provider_name,
            "model": f"{self.provider_name}-stub",
            "requires_api_key": False,
            "description": "FAILURE SIMULATION STUB — not a real LLM",
        }


class RateLimitProvider(_BaseFailingProvider):
    """Always raises HTTP 429 (provider rate limit / quota exhaustion)."""

    provider_name = "failing_stub_429"

    def complete(self, prompt, system="", max_tokens=2048, temperature=0.0,
                 json_mode=False, response_schema=None):
        raise ValueError("Groq API error 429: rate limit exceeded")


class ServiceUnavailableProvider(_BaseFailingProvider):
    """Always raises HTTP 503 (maximum combo retry limit reached)."""

    provider_name = "failing_stub_503"

    def complete(self, prompt, system="", max_tokens=2048, temperature=0.0,
                 json_mode=False, response_schema=None):
        raise ValueError("HTTP 503: Maximum combo retry limit reached")


class TimeoutProvider(_BaseFailingProvider):
    """Always raises a timeout error."""

    provider_name = "failing_stub_timeout"

    def complete(self, prompt, system="", max_tokens=2048, temperature=0.0,
                 json_mode=False, response_schema=None):
        raise ValueError("Groq request timed out after 90s")


class MalformedOutputProvider(_BaseFailingProvider):
    """Returns malformed output that fails schema validation."""

    provider_name = "failing_stub_malformed"

    def complete(self, prompt, system="", max_tokens=2048, temperature=0.0,
                 json_mode=False, response_schema=None):
        return "this is definitely not valid json {{{{"


class ToolIncompatibleProvider(_BaseFailingProvider):
    """Raises a tool-call incompatibility error."""

    provider_name = "failing_stub_tool_incompat"

    def complete(self, prompt, system="", max_tokens=2048, temperature=0.0,
                 json_mode=False, response_schema=None):
        raise ValueError("tool calling not supported for this model / incompatible tool-call JSON")


class MissingKeyProvider(_BaseFailingProvider):
    """Raises a missing API key error."""

    provider_name = "failing_stub_missing_key"

    def complete(self, prompt, system="", max_tokens=2048, temperature=0.0,
                 json_mode=False, response_schema=None):
        raise EnvironmentError("GROQ_API_KEY is not set")


def get_failing_provider(failure_mode: str) -> LLMProvider:
    """Get a failing stub provider by failure mode name.

    failure_mode: 429 | 503 | timeout | malformed | tool_incompat | missing_key
    """
    modes = {
        "429": RateLimitProvider,
        "503": ServiceUnavailableProvider,
        "timeout": TimeoutProvider,
        "malformed": MalformedOutputProvider,
        "tool_incompat": ToolIncompatibleProvider,
        "missing_key": MissingKeyProvider,
    }
    if failure_mode not in modes:
        raise ValueError(
            f"Unknown failure mode '{failure_mode}'. Valid: {sorted(modes)}"
        )
    return modes[failure_mode]()