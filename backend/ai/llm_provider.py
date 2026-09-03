"""Abstract LLM provider interface with pluggable backends.

Priority order for auto-selection:
1. Ollama (local, free, no API key)
2. Groq (cloud, fast, requires GROQ_API_KEY)
3. Gemini (cloud, requires GEMINI_API_KEY)
4. OpenRouter (cloud, requires OPENROUTER_API_KEY)
5. Anthropic Claude (requires ANTHROPIC_API_KEY)

Usage:
    provider = get_provider()
    result = provider.complete("Extract facts from...", system="You are...")
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from abc import ABC, abstractmethod
from typing import Optional

import httpx  # noqa: E402

from dataclasses import dataclass, field
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Native tool-calling types
# ---------------------------------------------------------------------------

@dataclass
class ToolCallInfo:
    """A single tool call returned by the LLM."""
    id: str
    function_name: str
    arguments: dict


@dataclass
class ToolCallResponse:
    """Response from complete_with_tools().

    When the model returns tool calls, *tool_calls* is non-empty and
    *content* may be None.  When the model produces a final text answer
    (no tool calls), *tool_calls* is empty and *content* holds the text.
    """
    content: str | None = None
    tool_calls: list[ToolCallInfo] = field(default_factory=list)
    finish_reason: str = "stop"


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

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> ToolCallResponse:
        """Complete with native tool/function calling support.

        Providers that support OpenAI-compatible tool calling (e.g. Groq)
        override this.  The default implementation returns text-only
        (no tool calls) so the agent loop can fall back to single-turn.

        *messages*: conversation history (role/content/tool_calls/tool_call_id)
        *tools*: list of OpenAI-compatible tool definitions
        *tool_choice*: "auto", "none", or {"type": "function", "function": {"name": ...}}
        """
        # Default: use complete() with a flattened prompt — no tool calling
        prompt = "\n".join(
            f"[{m['role'].upper()}] {m.get('content', '')}"
            for m in messages if m.get('content')
        )
        content = self.complete(
            prompt,
            system="",
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return ToolCallResponse(
            content=content,
            tool_calls=[],
            finish_reason="stop",
        )

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

    def chat_complete(
        self,
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
        response_schema=None,
    ):
        """Send a multi-turn message list and return the response.

        Unlike ``complete()`` which takes a single prompt string, this
        accepts the full conversation history.  Providers that support
        multi-turn chat (Groq, Gemini, OpenRouter, etc.) override this.
        The default flattens the conversation into a single prompt.
        """
        system = ""
        prompt_parts: list[str] = []
        for m in messages:
            if m["role"] == "system":
                system = m.get("content", "")
            elif m["role"] == "user":
                prompt_parts.append(m.get("content", ""))
            elif m["role"] == "assistant":
                prompt_parts.append(m.get("content", ""))
        prompt = "\n".join(prompt_parts)
        return self.complete(
            prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            response_schema=response_schema,
        )


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
    """Local Ollama LLM backend — free, no API key needed.

    Supports full solo execution of the Finance Controller LLM layer:
    - complete()
    - complete_json()
    - complete_with_tools() (native tool calling)
    - chat_complete() (multi-turn conversations)

    Intentionally local-only — never used in Vercel production.
    """

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
            "description": "Local Ollama — free, offline (local-only, not for Vercel production)",
        }

    def _ollama_chat(self, messages: list[dict], options: dict = None,
                     tools: list[dict] = None, format_json: bool = False) -> dict:
        """Send a chat request to Ollama and return the raw response dict."""
        import httpx

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": options or {},
            "think": False,
        }
        if format_json:
            payload["format"] = "json"

        # Ollama native tool calling — uses OpenAI-compatible tools format
        if tools:
            payload["tools"] = tools

        logger.info("Ollama request: model=%s, msgs=%d, tools=%s",
                    self.model, len(messages), bool(tools))
        t0 = time.time()

        try:
            with httpx.Client(timeout=120.0) as client:
                r = client.post(f"{self.base_url}/api/chat", json=payload)
                r.raise_for_status()
        except httpx.TimeoutException:
            raise ValueError(f"Ollama request timed out after 120s")
        except httpx.RequestError as e:
            raise ValueError(f"Ollama connection error: {e}")

        elapsed = time.time() - t0
        data = r.json()
        logger.info("Ollama response: elapsed=%.2fs", elapsed)
        return data

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
        response_schema=None,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        options = {
            "num_predict": max_tokens,
            "temperature": temperature,
        }
        data = self._ollama_chat(messages, options=options, format_json=json_mode)
        content = data.get("message", {}).get("content", "")
        logger.info("Ollama complete: %d chars", len(content))
        return content

    def _clean_messages_for_ollama(self, messages: list[dict]) -> list[dict]:
        """Clean messages for Ollama's API format.

        Ollama does not accept:
        - tool_calls field in assistant messages (it only returns them)
        - tool role messages (convert to user with prefix)
        This cleaning is required for multi-turn tool-calling loops.
        """
        clean = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "") or ""

            if role == "tool":
                # Ollama doesn't have a 'tool' role — convert to user message
                name = m.get("name", "unknown")
                clean.append({
                    "role": "user",
                    "content": f"[Tool result: {name}] {content}",
                })
            elif role == "assistant" and "tool_calls" in m:
                # Strip tool_calls — Ollama doesn't accept them as input
                clean_msg = {"role": "assistant", "content": content}
                if content:  # Only include assistant if it has content
                    clean.append(clean_msg)
            else:
                clean.append({"role": role, "content": content})
        return clean

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> ToolCallResponse:
        """Complete with Ollama native tool calling.

        Ollama uses the same OpenAI-compatible tool format:
        - Send tools=[...] in the request
        - Response message may contain tool_calls
        - Parse tool_calls and return as ToolCallResponse
        """
        # Clean messages — Ollama doesn't accept tool_calls or role="tool"
        cleaned = self._clean_messages_for_ollama(messages)
        options = {
            "num_predict": max_tokens,
            "temperature": temperature,
        }
        data = self._ollama_chat(cleaned, options=options, tools=tools)

        message = data.get("message", {})
        content = message.get("content")
        raw_tool_calls = message.get("tool_calls", []) or []

        # Parse tool calls into ToolCallInfo
        parsed_tool_calls: list[ToolCallInfo] = []
        for tc in raw_tool_calls:
            func = tc.get("function", {})
            func_name = func.get("name", "")
            args_raw = func.get("arguments", "{}")
            try:
                args_dict = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args_dict = {}
            parsed_tool_calls.append(
                ToolCallInfo(
                    id=tc.get("id", f"ollama_{func_name}_{uuid.uuid4().hex[:8]}"),
                    function_name=func_name,
                    arguments=args_dict,
                )
            )

        finish_reason = "tool_calls" if parsed_tool_calls else "stop"
        logger.info("Ollama tool response: content=%s, tool_calls=%d",
                    "present" if content else "None", len(parsed_tool_calls))

        return ToolCallResponse(
            content=content,
            tool_calls=parsed_tool_calls,
            finish_reason=finish_reason,
        )

    def chat_complete(
        self,
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
        response_schema=None,
    ):
        """Send a multi-turn message list to Ollama."""
        # Filter out tool_calls messages — Ollama doesn't accept them in input
        clean_messages = []
        for m in messages:
            clean = {"role": m["role"], "content": m.get("content", "")}
            if clean["content"]:
                clean_messages.append(clean)

        options = {
            "num_predict": max_tokens,
            "temperature": temperature,
        }
        data = self._ollama_chat(clean_messages, options=options, format_json=json_mode)
        content = data.get("message", {}).get("content", "")

        if json_mode and response_schema is not None:
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                # Try extracting JSON from text
                try:
                    return _parse_json_response(content)
                except ValueError:
                    pass

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
                # json.loads() failed — try the more permissive parser
                # which handles markdown fences and JSON boundary detection.
                try:
                    parsed_dict = _parse_json_response(content)
                    logger.info(
                        "Gemini response (text→structured fallback): "
                        "parsed in %.2fs",
                        elapsed,
                    )
                    return parsed_dict
                except ValueError:
                    # JSON is genuinely malformed (likely truncated) —
                    # return raw text for _parse_json_response() in
                    # complete_json().
                    logger.warning(
                        "Gemini text not valid JSON (likely truncated): "
                        "first 100 chars: %s",
                        content[:100],
                    )

        logger.info("Gemini response (text): %d chars in %.2fs", len(content), elapsed)
        return content

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> ToolCallResponse:
        """Complete with Gemini native function calling.

        Gemini supports function declarations via the google-genai SDK.
        We convert OpenAI-format tool definitions to Gemini's format,
        send them, and parse the response for function calls.
        """
        from google import genai

        if not self.api_key:
            raise EnvironmentError("GEMINI_API_KEY is not set")

        client = genai.Client(api_key=self.api_key)

        # Convert OpenAI-format tools to Gemini function declarations
        gemini_tools = []
        for tool in tools:
            func = tool.get("function", {})
            params = func.get("parameters", {})
            gemini_tools.append(
                genai.types.Tool(
                    function_declarations=[
                        genai.types.FunctionDeclaration(
                            name=func.get("name", ""),
                            description=func.get("description", ""),
                            parameters=genai.types.Schema(
                                type=genai.types.Type.OBJECT,
                                properties={
                                    k: genai.types.Schema(
                                        type=genai.types.Type.STRING,
                                        description=v.get("description", ""),
                                    )
                                    for k, v in params.get("properties", {}).items()
                                },
                                required=params.get("required", []),
                            ),
                        )
                    ]
                )
            )

        # Build conversation contents
        contents = []
        for m in messages:
            role = "model" if m["role"] == "assistant" else "user"
            if m["role"] == "system":
                continue  # Handled via system_instruction
            if m["role"] == "tool":
                role = "user"  # Tool results go as user messages
            contents.append(genai.types.Content(
                role=role,
                parts=[genai.types.Part.from_text(m.get("content", ""))],
            ))

        config = genai.types.GenerateContentConfig(
            tools=gemini_tools if gemini_tools else None,
            max_output_tokens=max_tokens,
            temperature=temperature,
        )

        t0 = time.time()
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            elapsed = time.time() - t0
            error_msg = str(e)
            if "429" in error_msg or "rate" in error_msg.lower():
                raise ValueError(f"Gemini API error 429: rate limit exceeded")
            if "timeout" in error_msg.lower():
                raise ValueError(f"Gemini request timed out after {elapsed:.0f}s")
            raise ValueError(f"Gemini API error: {error_msg}")

        elapsed = time.time() - t0

        # Parse function calls from response
        parsed_tool_calls: list[ToolCallInfo] = []
        content_text = None

        if response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        fc = part.function_call
                        args = dict(fc.args) if fc.args else {}
                        parsed_tool_calls.append(
                            ToolCallInfo(
                                id=f"gemini_{fc.name}_{uuid.uuid4().hex[:8]}",
                                function_name=fc.name,
                                arguments=args,
                            )
                        )
                    elif hasattr(part, "text") and part.text:
                        content_text = part.text

        finish_reason = "tool_calls" if parsed_tool_calls else "stop"
        logger.info(
            "Gemini tool response: content=%s, tool_calls=%d, elapsed=%.2fs",
            "present" if content_text else "None",
            len(parsed_tool_calls), elapsed,
        )

        return ToolCallResponse(
            content=content_text,
            tool_calls=parsed_tool_calls,
            finish_reason=finish_reason,
        )

    def chat_complete(
        self,
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
        response_schema=None,
    ):
        """Send a multi-turn message list to Gemini."""
        from google import genai

        # Build conversation contents
        contents = []
        system_instruction = None
        for m in messages:
            if m["role"] == "system":
                system_instruction = m.get("content", "")
                continue
            role = "model" if m["role"] == "assistant" else "user"
            if m["role"] == "tool":
                role = "user"
            contents.append(genai.types.Content(
                role=role,
                parts=[genai.types.Part.from_text(m.get("content", ""))],
            ))

        return self.complete(
            prompt="",
            system=system_instruction or "",
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            response_schema=response_schema,
        ) if not contents else self._gemini_chat_complete(
            contents, system_instruction, max_tokens, temperature,
            json_mode, response_schema,
        )

    def _gemini_chat_complete(
        self,
        contents: list,
        system_instruction: str | None,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        response_schema=None,
    ):
        """Internal multi-turn chat complete via Gemini SDK."""
        from google import genai

        if not self.api_key:
            raise EnvironmentError("GEMINI_API_KEY is not set")

        client = genai.Client(api_key=self.api_key)

        config_kwargs = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
        if response_schema is not None:
            config_kwargs["response_schema"] = response_schema

        config = genai.types.GenerateContentConfig(**config_kwargs)

        t0 = time.time()
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            elapsed = time.time() - t0
            error_msg = str(e)
            if "429" in error_msg or "rate" in error_msg.lower():
                raise ValueError(f"Gemini API error 429: rate limit exceeded")
            if "timeout" in error_msg.lower():
                raise ValueError(f"Gemini request timed out after {elapsed:.0f}s")
            raise ValueError(f"Gemini API error: {error_msg}")

        elapsed = time.time() - t0

        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            if hasattr(parsed, "model_dump"):
                return parsed.model_dump()
            elif isinstance(parsed, dict):
                return parsed
            else:
                return str(parsed)

        if not response.candidates:
            raise ValueError("Gemini returned no candidates")

        parts = response.candidates[0].content.parts
        if not parts:
            raise ValueError("Gemini returned empty response")

        content = parts[0].text
        if not content:
            raise ValueError("Gemini returned empty text")

        if response_schema is not None:
            try:
                return json.loads(content)
            except (json.JSONDecodeError, ValueError):
                try:
                    return _parse_json_response(content)
                except ValueError:
                    pass

        logger.info("Gemini chat response: %d chars in %.2fs", len(content), elapsed)
        return content


# ---------------------------------------------------------------------------
# Groq provider
# ---------------------------------------------------------------------------


def _pydantic_to_strict_json_schema(model_class) -> dict:
    """Convert a Pydantic model to a Groq strict-mode JSON Schema.

    Groq strict mode requires:
    - All fields in "required"
    - ``additionalProperties: false`` on every object
    - Optional fields represented as nullable unions:
      ``{"anyOf": [{"type": "..."}, {"type": "null"}]}``
    """
    raw = model_class.model_json_schema()

    def _make_strict(schema: dict) -> dict:
        """Recursively enforce Groq strict-mode rules."""
        if not isinstance(schema, dict):
            return schema

        s = dict(schema)

        # Handle $ref — resolve it from the top-level $defs
        if "$ref" in s:
            ref_name = s["$ref"].rsplit("/", 1)[-1]
            defs = raw.get("$defs", {})
            if ref_name in defs:
                return _make_strict(defs[ref_name])
            return s

        # Handle allOf (Pydantic wraps single inheritance in allOf)
        if "allOf" in s and len(s["allOf"]) == 1:
            return _make_strict(s["allOf"][0])

        # Process object types
        if s.get("type") == "object":
            s["additionalProperties"] = False
            props = s.get("properties", {})
            required_fields = list(props.keys())

            for prop_name, prop_schema in props.items():
                props[prop_name] = _make_strict(prop_schema)

            # Ensure all fields are in required
            s["required"] = required_fields

        # Handle arrays
        if s.get("type") == "array" and "items" in s:
            s["items"] = _make_strict(s["items"])

        # Handle optional/nullable fields: convert anyOf with null to
        # a proper nullable union.
        if "anyOf" in s:
            # Check if it's a nullable pattern: one type + null
            types = [item.get("type") for item in s["anyOf"] if isinstance(item, dict)]
            if "null" in types and len(types) == 2:
                non_null = [item for item in s["anyOf"] if item.get("type") != "null"]
                if len(non_null) == 1:
                    inner = _make_strict(non_null[0])
                    s = {"anyOf": [inner, {"type": "null"}]}
            else:
                s["anyOf"] = [_make_strict(item) for item in s["anyOf"]]

        return s

    result = _make_strict(raw)
    # Remove top-level $defs — already inlined
    result.pop("$defs", None)
    return result


class GroqProvider(LLMProvider):
    """Groq cloud LLM backend — requires GROQ_API_KEY.

    Uses Groq's OpenAI-compatible API with strict structured output.
    Recommended model: openai/gpt-oss-120b.
    """

    def __init__(self, api_key: str = "", model: str = "openai/gpt-oss-120b"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.groq.com/openai/v1"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def provider_info(self) -> dict:
        return {
            "provider": "groq",
            "model": self.model,
            "requires_api_key": True,
            "description": f"Groq ({self.model})",
        }

    def _groq_request(self, payload: dict, timeout: float = 60.0) -> dict:
        """Send a request to the Groq API and return parsed JSON."""
        if not self.api_key:
            raise EnvironmentError("GROQ_API_KEY is not set")

        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=timeout,
            )
        except httpx.TimeoutException:
            raise ValueError(f"Groq request timed out after {timeout}s")
        except httpx.RequestError as e:
            raise ValueError(f"Groq connection error: {e}")

        if resp.status_code != 200:
            error_body = resp.text[:500]
            raise ValueError(
                f"Groq API error {resp.status_code}: {error_body}"
            )

        return resp.json()

    def chat_complete(
        self,
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
        response_schema=None,
    ):
        """Send a multi-turn message list to Groq."""
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if response_schema is not None and json_mode:
            strict_schema = _pydantic_to_strict_json_schema(response_schema)
            schema_name = getattr(response_schema, "__name__", "response")
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": strict_schema,
                },
            }
        elif json_mode:
            payload["response_format"] = {"type": "json_object"}

        t0 = time.time()
        data = self._groq_request(payload)
        elapsed = time.time() - t0

        choices = data.get("choices", [])
        if not choices:
            raise ValueError("Groq returned no choices")

        message = choices[0].get("message", {})
        content = message.get("content") or ""
        finish_reason = choices[0].get("finish_reason")

        if not content and finish_reason != "length":
            raise ValueError(
                f"Groq returned empty content. finish_reason={finish_reason}"
            )

        logger.info(
            "Groq chat response: %d chars, finish_reason=%s, elapsed=%.2fs",
            len(content), finish_reason, elapsed,
        )

        if response_schema is not None and json_mode:
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                logger.warning("Groq structured output not valid JSON")

        return content

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> ToolCallResponse:
        """Complete with native tool/function calling via the Groq API.

        Sends the full conversation history plus tool definitions.
        Inspects ``response.choices[0].message.tool_calls`` for
        native tool calls, or returns text content if the model
        chose to respond without calling tools.
        """
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        logger.info(
            "Groq tool-calling request: model=%s, messages=%d, tools=%d",
            self.model, len(messages), len(tools),
        )
        t0 = time.time()

        try:
            data = self._groq_request(payload, timeout=90.0)
        except Exception as e:
            logger.error("Groq tool-calling request failed: %s", str(e))
            raise

        elapsed = time.time() - t0

        choices = data.get("choices", [])
        if not choices:
            raise ValueError("Groq returned no choices")

        choice = choices[0]
        message = choice.get("message", {})
        content = message.get("content")  # may be None when tool calls present
        finish_reason = choice.get("finish_reason", "stop")

        # Parse native tool calls
        raw_tool_calls = message.get("tool_calls", []) or []
        parsed_tool_calls: list[ToolCallInfo] = []
        for tc in raw_tool_calls:
            func = tc.get("function", {})
            func_name = func.get("name", "")
            args_raw = func.get("arguments", "{}")
            # Parse arguments from JSON string
            try:
                args_dict = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args_dict = {}
            parsed_tool_calls.append(
                ToolCallInfo(
                    id=tc.get("id", ""),
                    function_name=func_name,
                    arguments=args_dict,
                )
            )

        logger.info(
            "Groq tool-calling response: content=%s, tool_calls=%d, finish_reason=%s, elapsed=%.2fs",
            "present" if content else "None",
            len(parsed_tool_calls),
            finish_reason,
            elapsed,
        )

        return ToolCallResponse(
            content=content,
            tool_calls=parsed_tool_calls,
            finish_reason=finish_reason,
        )

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
        response_schema=None,
    ):
        if not self.api_key:
            raise EnvironmentError("GROQ_API_KEY is not set")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Build response_format for structured output.
        if response_schema is not None and json_mode:
            # Strict mode: convert Pydantic schema to Groq-compatible JSON Schema.
            strict_schema = _pydantic_to_strict_json_schema(response_schema)
            schema_name = getattr(response_schema, "__name__", "response")
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": strict_schema,
                },
            }
        elif json_mode:
            payload["response_format"] = {"type": "json_object"}

        logger.info(
            "Groq request: model=%s, prompt_len=%d, json_mode=%s, has_schema=%s",
            self.model, len(prompt), json_mode, response_schema is not None,
        )
        t0 = time.time()

        data = self._groq_request(payload)
        elapsed = time.time() - t0

        choices = data.get("choices", [])
        if not choices:
            raise ValueError("Groq returned no choices")

        message = choices[0].get("message", {})
        content = message.get("content") or ""

        # Handle refusal (structured output safety)
        finish_reason = choices[0].get("finish_reason")
        if finish_reason == "length":
            logger.warning(
                "Groq response truncated (finish_reason=length): "
                "content_len=%d, elapsed=%.2fs",
                len(content), elapsed,
            )

        if not content:
            raise ValueError(
                f"Groq returned empty content. "
                f"finish_reason={finish_reason}, "
                f"response_keys={list(data.keys())}"
            )

        logger.info(
            "Groq response: %d chars, finish_reason=%s, elapsed=%.2fs",
            len(content), finish_reason, elapsed,
        )

        # When strict structured output was used, the response IS valid JSON
        # conforming to the schema.  Parse and return as dict to avoid
        # the redundant json.loads round-trip.
        if response_schema is not None and json_mode:
            try:
                parsed = json.loads(content)
                logger.info(
                    "Groq structured response parsed: %d keys",
                    len(parsed) if isinstance(parsed, dict) else 0,
                )
                return parsed
            except json.JSONDecodeError:
                # Strict mode should never produce invalid JSON, but be safe.
                logger.warning(
                    "Groq strict output not valid JSON (should not happen): "
                    "first 100 chars: %s",
                    content[:100],
                )

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
# Provider error classification
# ---------------------------------------------------------------------------

PROVIDER_ERROR_CATEGORIES = {
    "provider_error": "General provider failure",
    "rate_limit": "Rate limit / quota exceeded (429)",
    "timeout": "Request timed out",
    "schema_error": "Structured output / schema validation failure",
    "tool_error": "Tool calling error",
    "invalid_arguments": "Invalid tool arguments",
    "unavailable": "Provider not available",
}


def classify_provider_error(error: Exception) -> str:
    """Classify an exception into a safe failure category."""
    msg = str(error).lower()
    if "429" in msg or "rate" in msg or "quota" in msg:
        return "rate_limit"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "api_key" in msg or "invalid" in msg or "unauthorized" in msg:
        return "provider_error"
    if "connection" in msg or "unreachable" in msg or "refused" in msg:
        return "unavailable"
    if "schema" in msg or "json" in msg or "parse" in msg:
        return "schema_error"
    return "provider_error"


# ---------------------------------------------------------------------------
# Production provider selection
# ---------------------------------------------------------------------------

def is_production() -> bool:
    """Detect if running in Vercel / production environment."""
    return (
        os.environ.get("VERCEL") is not None
        or os.environ.get("PRODUCTION_PROVIDER") is not None
        or os.environ.get("ENVIRONMENT", "").lower() in ("production", "prod")
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_provider_instance: Optional[LLMProvider] = None


def get_provider() -> LLMProvider:
    """Auto-select and cache the best available provider.

    In production (Vercel): uses explicit PRODUCTION_PROVIDER or falls
    through to Groq (cloud) > Gemini (cloud) > OpenRouter > Anthropic.
    NEVER attempts localhost Ollama in production.

    In development: Ollama (local/free) > Groq > Gemini > OpenRouter > Anthropic.
    """
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    # --- Production mode: explicit provider, no localhost Ollama ---
    if is_production():
        prod_provider = os.environ.get("PRODUCTION_PROVIDER", "groq")
        logger.info("Production mode: PRODUCTION_PROVIDER=%s", prod_provider)

        if prod_provider == "groq":
            groq_key = os.environ.get("GROQ_API_KEY", "")
            groq_model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
            groq = GroqProvider(api_key=groq_key, model=groq_model)
            if groq.is_available():
                _provider_instance = groq
                return _provider_instance
            raise EnvironmentError(
                "Production provider 'groq' configured but GROQ_API_KEY not set."
            )

        elif prod_provider == "gemini":
            gemini_key = os.environ.get("GEMINI_API_KEY", "")
            gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
            gemini = GeminiProvider(api_key=gemini_key, model=gemini_model)
            if gemini.is_available():
                _provider_instance = gemini
                return _provider_instance
            raise EnvironmentError(
                "Production provider 'gemini' configured but GEMINI_API_KEY not set."
            )

        elif prod_provider == "ollama":
            raise EnvironmentError(
                "PRODUCTION_PROVIDER=ollama is not allowed. "
                "Ollama is intentionally local-only and not part of "
                "Vercel production deployment."
            )

        else:
            raise EnvironmentError(
                f"Unknown PRODUCTION_PROVIDER: {prod_provider}. "
                "Valid options: groq, gemini"
            )

    # --- Development mode: try providers in priority order ---
    # 1. Ollama (local, free, no API key)
    model_name = os.environ.get("OLLAMA_MODEL", "qwen3.5:latest")
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama = OllamaProvider(base_url=base_url, model=model_name)
    if ollama.is_available():
        logger.info("Using Ollama provider (model=%s) [development only]", model_name)
        _provider_instance = ollama
        return _provider_instance

    # 2. Groq (cloud, fast, requires GROQ_API_KEY)
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if groq_key:
        groq_model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
        groq = GroqProvider(api_key=groq_key, model=groq_model)
        if groq.is_available():
            logger.info("Using Groq provider (model=%s)", groq_model)
            _provider_instance = groq
            return _provider_instance

    # 3. Gemini (cloud, requires GEMINI_API_KEY)
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        gemini = GeminiProvider(api_key=gemini_key, model=gemini_model)
        if gemini.is_available():
            logger.info("Using Gemini provider (model=%s)", gemini_model)
            _provider_instance = gemini
            return _provider_instance

    # 4. OpenRouter (cloud, requires OPENROUTER_API_KEY)
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

    # 5. Anthropic (cloud, requires ANTHROPIC_API_KEY)
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
        "  2. Set GROQ_API_KEY environment variable (recommended for production).\n"
        "  3. Set GEMINI_API_KEY environment variable.\n"
        "  4. Set OPENROUTER_API_KEY environment variable.\n"
        "  5. Set ANTHROPIC_API_KEY environment variable.\n"
        "Use seeded demo data for offline operation."
    )


def get_provider_by_name(name: str) -> LLMProvider:
    """Get a specific provider by name (for benchmark parity tests)."""
    name = name.lower().strip()
    if name == "ollama":
        model_name = os.environ.get("OLLAMA_MODEL", "qwen3.5:latest")
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        return OllamaProvider(base_url=base_url, model=model_name)
    elif name == "groq":
        groq_key = os.environ.get("GROQ_API_KEY", "")
        groq_model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
        return GroqProvider(api_key=groq_key, model=groq_model)
    elif name == "gemini":
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        return GeminiProvider(api_key=gemini_key, model=gemini_model)
    else:
        raise ValueError(f"Unknown provider: {name}. Valid: ollama, groq, gemini")


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
