"""Abstract LLM provider interface with pluggable backends.

Priority order for auto-selection:
1. Ollama (local, free, no API key)
2. OpenRouter (cloud, requires OPENROUTER_API_KEY)
3. Anthropic Claude (requires ANTHROPIC_API_KEY)

Usage:
    provider = get_provider()
    result = provider.complete("Extract facts from...", system="You are...")
"""
from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured-output schemas for Gemini native JSON mode
# ---------------------------------------------------------------------------

class ExtractionFact(BaseModel):
    """Single extracted fact from evidence."""
    fact_type: str
    value: str
    amount: Optional[float] = None
    date: Optional[str] = None
    evidence_quote: str


class ExtractionSchema(BaseModel):
    """Schema for evidence extraction responses."""
    facts: list[ExtractionFact]


class ReasoningClaim(BaseModel):
    """Single claim from the reasoning step."""
    claim_type: str
    policy_clause_id: str
    evidence_ids: list[str]
    reasoning: str


class ReasoningSchema(BaseModel):
    """Schema for reasoning responses."""
    claims: list[ReasoningClaim]
    classification: str
    confidence: float
    reasoning_summary: str


class LLMProvider(ABC):
    """Abstract interface for LLM backends."""

    @abstractmethod
    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
        response_schema=None,
    ):
        """Send a prompt and return the response.

        Returns a ``str`` for most providers.  Providers with native
        structured output (e.g. Gemini with *response_schema*) may
        return a ``dict`` or Pydantic model — callers should handle
        both cases.

        When *json_mode* is True the provider should request structured
        JSON output from the model (e.g. response_format).

        *response_schema* is an optional Pydantic model or JSON Schema
        dict.  Providers that support native structured output (e.g.
        Gemini) use it to constrain the response.  Others ignore it.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this provider can serve requests right now."""
        ...

    @abstractmethod
    def provider_info(self) -> dict:
        """Return metadata about this provider for the UI."""
        ...

    def complete_json(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.0,
        response_schema=None,
    ) -> dict:
        """Complete and parse the response as JSON.

        Requests json_mode from the provider when supported.  If
        *response_schema* is a Pydantic model, providers with native
        structured output will use it to guarantee valid JSON.

        When the provider returns an already-parsed dict (e.g. Gemini
        with response.parsed), it is passed through directly — no
        redundant text→dict round-trip.
        """
        result = self.complete(
            prompt, system=system, max_tokens=max_tokens,
            temperature=temperature, json_mode=True,
            response_schema=response_schema,
        )
        # Native structured output providers (Gemini) return a dict/model
        # directly — skip the fragile string→JSON round-trip.
        if isinstance(result, dict):
            return result
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return _parse_json_response(result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_response(text: str) -> dict:
    """Extract and parse JSON from LLM response text."""
    text = text.strip()

    # Strip thinking tags if present (Qwen3.5 thinking mode)
    if "<think>" in text and "</think>" in text:
        # Remove everything between <think> tags
        parts = text.split("<think>")
        text = parts[0] + parts[-1]
        text = text.strip()

    # Handle markdown code blocks
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON object in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Failed to parse JSON from LLM response: {text[:200]}...")


# ---------------------------------------------------------------------------
# Ollama provider
# ---------------------------------------------------------------------------

class OllamaProvider(LLMProvider):
    """Local Ollama LLM backend — free, no API key needed."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3.5:latest",
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def is_available(self) -> bool:
        try:
            import httpx
            r = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            if r.status_code == 200:
                models = r.json().get("models", [])
                return any(m.get("name") == self.model for m in models)
            return False
        except Exception:
            return False

    def provider_info(self) -> dict:
        return {
            "provider": "ollama",
            "model": self.model,
            "base_url": self.base_url,
            "requires_api_key": False,
            "description": "Local Ollama — free, offline",
        }

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
        response_schema=None,
    ) -> str:
        import httpx

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
            # Disable thinking for faster structured output
            "think": False,
        }
        # Ollama supports format: "json" for JSON mode
        if json_mode:
            payload["format"] = "json"

        logger.info("Ollama request: model=%s, prompt_len=%d", self.model, len(prompt))
        t0 = time.time()

        with httpx.Client(timeout=120.0) as client:
            r = client.post(f"{self.base_url}/api/chat", json=payload)
            r.raise_for_status()

        elapsed = time.time() - t0
        data = r.json()
        content = data.get("message", {}).get("content", "")
        logger.info("Ollama response: %d chars in %.2fs", len(content), elapsed)
        return content


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------

class AnthropicProvider(LLMProvider):
    """Anthropic Claude backend — requires ANTHROPIC_API_KEY."""

    def __init__(self, model: str = "claude-sonnet-4-20250514"):
        self.model = model

    def is_available(self) -> bool:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return False
        try:
            import anthropic  # noqa: F401
            return True
        except ImportError:
            return False

    def provider_info(self) -> dict:
        return {
            "provider": "anthropic",
            "model": self.model,
            "requires_api_key": True,
            "description": "Anthropic Claude (requires API key)",
        }

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
        response_schema=None,
    ) -> str:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY is not set")

        client = anthropic.Anthropic(api_key=api_key)

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if temperature:
            kwargs["temperature"] = temperature

        logger.info("Anthropic request: model=%s, prompt_len=%d", self.model, len(prompt))
        t0 = time.time()

        message = client.messages.create(**kwargs)

        elapsed = time.time() - t0
        content = message.content[0].text
        logger.info("Anthropic response: %d chars in %.2fs", len(content), elapsed)
        return content


# ---------------------------------------------------------------------------
# Gemini provider
# ---------------------------------------------------------------------------

class GeminiProvider(LLMProvider):
    """Google Gemini backend — requires GEMINI_API_KEY.

    Uses the official google-genai SDK with native structured JSON output.
    Recommended model: gemini-2.5-flash (fast, cheap, strong JSON mode).
    """

    def __init__(self, api_key: str = "", model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        try:
            from google import genai  # noqa: F401
            return True
        except ImportError:
            return False

    def provider_info(self) -> dict:
        return {
            "provider": "gemini",
            "model": self.model,
            "requires_api_key": True,
            "description": f"Google Gemini ({self.model})",
        }

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
        response_schema=None,
    ) -> str:
        from google import genai

        if not self.api_key:
            raise EnvironmentError("GEMINI_API_KEY is not set")

        client = genai.Client(api_key=self.api_key)

        # Build config — system_instruction is a first-class field in the SDK.
        config_kwargs = {
            "system_instruction": system or None,
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }

        # Request structured JSON output when the caller needs it.
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"

        # When a Pydantic schema is provided, Gemini will guarantee valid
        # JSON conforming to that schema and populate response.parsed.
        if response_schema is not None:
            config_kwargs["response_schema"] = response_schema

        config = genai.types.GenerateContentConfig(**config_kwargs)

        logger.info("Gemini request: model=%s, prompt_len=%d, json_mode=%s", self.model, len(prompt), json_mode)
        t0 = time.time()

        try:
            response = client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
        except Exception as e:
            elapsed = time.time() - t0
            error_msg = str(e)
            # Classify common errors
            if "API_KEY_INVALID" in error_msg or "invalid api key" in error_msg.lower():
                raise ValueError(f"Gemini API error: invalid API key")
            if "quota" in error_msg.lower() or "rate" in error_msg.lower():
                raise ValueError(f"Gemini API error: quota/rate limit exceeded")
            if "timeout" in error_msg.lower() or "deadline" in error_msg.lower():
                raise ValueError(f"Gemini request timed out after {elapsed:.0f}s")
            raise ValueError(f"Gemini API error: {error_msg}")

        elapsed = time.time() - t0

        # --- Extract the response text ---
        # Prefer response.parsed (native structured output from SDK) when
        # a response_schema was used.  Fall back to response.text.
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            # Return the native structured object directly — no redundant
            # json.dumps round-trip.  complete_json() handles both dicts
            # and Pydantic models.
            if hasattr(parsed, "model_dump"):
                result = parsed.model_dump()
                logger.info(
                    "Gemini response (structured): %d keys in %.2fs",
                    len(result), elapsed,
                )
                return result
            elif isinstance(parsed, dict):
                logger.info(
                    "Gemini response (dict): %d keys in %.2fs",
                    len(parsed), elapsed,
                )
                return parsed
            else:
                content = str(parsed)
                logger.info(
                    "Gemini response (parsed→str): %d chars in %.2fs",
                    len(content), elapsed,
                )
                return content

        # --- response.parsed was None ---
        # The SDK's model_validate_json() silently swallowed a ValidationError
        # or JSONDecodeError.  This happens when Gemini returns valid JSON but
        # with a preamble, or when the output is truncated.  We try to parse
        # the raw text ourselves.
        if not response.candidates:
            raise ValueError("Gemini returned no candidates")

        parts = response.candidates[0].content.parts
        if not parts:
            raise ValueError("Gemini returned empty response (no parts)")

        content = parts[0].text
        if not content:
            raise ValueError("Gemini returned empty text in response")

        # Log safe diagnostic metadata — never log raw evidence or API keys.
        finish_reason = None
        try:
            finish_reason = response.candidates[0].finish_reason
        except (AttributeError, IndexError):
            pass
        logger.warning(
            "Gemini response.parsed was None (SDK validation failed). "
            "finish_reason=%s, text_len=%d, elapsed=%.2fs",
            finish_reason, len(content), elapsed,
        )

        # When response_schema was provided, Gemini guarantees valid JSON
        # at the API level.  The SDK's client-side model_validate_json()
        # may fail on minor issues (preamble text, etc.).  Parse directly.
        if response_schema is not None:
            try:
                parsed_dict = json.loads(content)
                logger.info(
                    "Gemini response (text→json fallback): parsed in %.2fs",
                    elapsed,
                )
                return parsed_dict
            except (json.JSONDecodeError, ValueError):
                # JSON is genuinely malformed (likely truncated) —
                # return raw text for _parse_json_response() in complete_json().
                logger.warning(
                    "Gemini text not valid JSON (likely truncated): "
                    "first 100 chars: %s",
                    content[:100],
                )

        logger.info("Gemini response (text): %d chars in %.2fs", len(content), elapsed)
        return content


# ---------------------------------------------------------------------------
# OpenRouter provider
# ---------------------------------------------------------------------------

class OpenRouterProvider(LLMProvider):
    """OpenRouter cloud LLM backend — requires OPENROUTER_API_KEY.

    Uses the OpenAI-compatible chat completions endpoint.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "openrouter/free",
        base_url: str = "https://openrouter.ai/api/v1",
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def provider_info(self) -> dict:
        return {
            "provider": "openrouter",
            "model": self.model,
            "requires_api_key": True,
            "description": f"OpenRouter cloud LLM ({self.model})",
        }

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
        response_schema=None,
    ) -> str:
        import httpx

        if not self.api_key:
            raise EnvironmentError("OPENROUTER_API_KEY is not set")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Request structured JSON output when the model supports it.
        # Not all models/endpoints honour this, so we still parse
        # and validate the response ourselves.
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://entitlementledger.app",
            "X-Title": "EntitlementLedger",
        }

        url = f"{self.base_url}/chat/completions"

        logger.info("OpenRouter request: model=%s, prompt_len=%d", self.model, len(prompt))
        t0 = time.time()

        try:
            with httpx.Client(timeout=120.0) as client:
                r = client.post(url, json=payload, headers=headers)
                r.raise_for_status()
        except httpx.TimeoutException:
            raise ValueError(f"OpenRouter request timed out after 120s")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            try:
                body = e.response.json()
                detail = body.get("error", {}).get("message", str(e))
            except Exception:
                detail = str(e)
            raise ValueError(f"OpenRouter API error ({status}): {detail}")
        except httpx.RequestError as e:
            raise ValueError(f"OpenRouter connection error: {e}")

        elapsed = time.time() - t0
        data = r.json()

        # Handle OpenAI-compatible error responses
        if "error" in data:
            raise ValueError(f"OpenRouter error: {data['error'].get('message', str(data['error']))}")

        choices = data.get("choices", [])
        if not choices:
            raise ValueError("OpenRouter returned no choices")

        message = choices[0].get("message", {})

        # Safe debug logging: response structure only, never content/keys
        logger.info(
            "OpenRouter response structure: choice_keys=%s, message_keys=%s, "
            "finish_reason=%s, has_content=%s, has_reasoning=%s",
            list(choices[0].keys()),
            list(message.keys()),
            choices[0].get("finish_reason"),
            bool(message.get("content")),
            bool(message.get("reasoning_content")),
        )

        # Extract content: try standard content first, then reasoning_content
        # fallback.  Some reasoning models (DeepSeek R1, Qwen w/ thinking)
        # return their output in reasoning_content while content is null/empty.
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""

        # Prefer content if present; fall back to reasoning_content
        effective = content or reasoning

        if not effective:
            logger.error(
                "OpenRouter returned no usable content. response_keys=%s, "
                "message_keys=%s",
                list(data.keys()),
                list(message.keys()),
            )
            raise ValueError(
                "OpenRouter returned empty content. "
                f"Model may not support this request format. "
                f"Response keys: {list(data.keys())}"
            )

        logger.info(
            "OpenRouter response: %d chars (content=%d, reasoning=%d) in %.2fs",
            len(effective), len(content), len(reasoning), elapsed,
        )
        return effective


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_provider_instance: Optional[LLMProvider] = None


def get_provider() -> LLMProvider:
    """Auto-select and cache the best available provider.

    Priority: Ollama (local/free) > Gemini (cloud) > OpenRouter (cloud) > Anthropic (cloud).
    """
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    # Try Ollama first (local, free, no API key)
    model_name = os.environ.get("OLLAMA_MODEL", "qwen3.5:latest")
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama = OllamaProvider(base_url=base_url, model=model_name)
    if ollama.is_available():
        logger.info("Using Ollama provider (model=%s)", model_name)
        _provider_instance = ollama
        return _provider_instance

    # Try Gemini if API key is present (preferred cloud provider)
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        gemini = GeminiProvider(api_key=gemini_key, model=gemini_model)
        if gemini.is_available():
            logger.info("Using Gemini provider (model=%s)", gemini_model)
            _provider_instance = gemini
            return _provider_instance

    # Try OpenRouter if API key is present
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        openrouter_model = os.environ.get("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")
        openrouter_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        openrouter = OpenRouterProvider(
            api_key=openrouter_key,
            model=openrouter_model,
            base_url=openrouter_url,
        )
        logger.info("Using OpenRouter provider (model=%s)", openrouter_model)
        _provider_instance = openrouter
        return _provider_instance

    # Fall back to Anthropic if API key is present
    if os.environ.get("ANTHROPIC_API_KEY"):
        anthropic_model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        claude = AnthropicProvider(model=anthropic_model)
        if claude.is_available():
            logger.info("Using Anthropic provider (model=%s)", anthropic_model)
            _provider_instance = claude
            return _provider_instance

    # Nothing available
    raise EnvironmentError(
        "No LLM provider available. Either:\n"
        "  1. Start Ollama with a model: ollama serve && ollama pull qwen3.5\n"
        "  2. Set GEMINI_API_KEY environment variable (recommended for production).\n"
        "  3. Set OPENROUTER_API_KEY environment variable.\n"
        "  4. Set ANTHROPIC_API_KEY environment variable.\n"
        "Use seeded demo data for offline operation."
    )


def reset_provider() -> None:
    """Reset the cached provider (for testing)."""
    global _provider_instance
    _provider_instance = None


def is_ai_available() -> bool:
    """Check if any LLM provider is available."""
    try:
        get_provider()
        return True
    except EnvironmentError:
        return False
