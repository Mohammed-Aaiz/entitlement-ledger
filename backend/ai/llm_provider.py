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

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Abstract interface for LLM backends."""

    @abstractmethod
    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> str:
        """Send a prompt and return the raw text response."""
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
    ) -> dict:
        """Complete and parse the response as JSON.

        Handles markdown code fences automatically.
        """
        raw = self.complete(prompt, system=system, max_tokens=max_tokens, temperature=temperature)
        return _parse_json_response(raw)


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

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise ValueError("OpenRouter returned empty content")

        logger.info("OpenRouter response: %d chars in %.2fs", len(content), elapsed)
        return content


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_provider_instance: Optional[LLMProvider] = None


def get_provider() -> LLMProvider:
    """Auto-select and cache the best available provider.

    Priority: Ollama (local/free) > OpenRouter (cloud) > Anthropic (cloud).
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

    # Try OpenRouter if API key is present
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        openrouter_model = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
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
        "  2. Set OPENROUTER_API_KEY environment variable.\n"
        "  3. Set ANTHROPIC_API_KEY environment variable.\n"
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
