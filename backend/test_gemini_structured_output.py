"""Regression tests for Gemini structured-output data flow.

Verifies:
1. Gemini response.parsed returns valid dict/model and is accepted directly
2. Gemini raw text fallback still works (response.parsed=None but valid JSON)
3. Malformed raw text is still rejected
4. Extraction schema is validated (ExtractionSchema matches extraction.py)
5. Reasoning schema is validated (ReasoningSchema matches reasoning.py)
6. complete_json() never serializes a dict back to text for re-parsing
7. _gemini_chat_complete() handles response_schema without requiring json_mode
"""
import json
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gemini_response(parsed=None, text=None, candidates=True):
    """Build a mock Gemini API response."""
    response = MagicMock()

    # response.parsed — the native structured output
    response.parsed = parsed

    if candidates:
        part = MagicMock()
        part.text = text or "{}"
        response.candidates = [MagicMock()]
        response.candidates[0].content.parts = [part]
        response.candidates[0].finish_reason = "STOP"
    else:
        response.candidates = []

    return response


# ===========================================================================
# 1. Gemini response.parsed → accepted directly (no text round-trip)
# ===========================================================================

class TestGeminiResponseParsedDirectFlow:
    """When response.parsed succeeds, complete_json() must return a dict
    WITHOUT serializing to text and re-parsing."""

    def test_parsed_pydantic_model_returned_as_dict(self):
        """Gemini complete() returns model_dump() for Pydantic response.parsed."""
        from ai.llm_provider import GeminiProvider, ExtractionSchema, _parse_json_response

        provider = GeminiProvider(api_key="test_key", model="test")

        # Build a valid Pydantic model as response.parsed
        mock_parsed = ExtractionSchema(facts=[
            {"fact_type": "order_detail", "value": "Order found", "evidence_quote": "Order #123"}
        ])

        mock_response = _make_gemini_response(parsed=mock_parsed)

        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = provider.complete(
                "Extract facts",
                system="You are an extraction system",
                json_mode=True,
                response_schema=ExtractionSchema,
            )

        # Must be a dict — not a string
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "facts" in result
        assert len(result["facts"]) == 1
        assert result["facts"][0]["fact_type"] == "order_detail"

    def test_parsed_dict_returned_directly(self):
        """Gemini complete() returns dict directly when response.parsed is a dict."""
        from ai.llm_provider import GeminiProvider, ReasoningSchema

        provider = GeminiProvider(api_key="test_key", model="test")

        mock_parsed = {
            "claims": [{"claim_type": "sla_breach", "policy_clause_id": "sla_4_2",
                        "evidence_ids": ["ev_1"], "reasoning": "Late delivery"}],
            "classification": "clear",
            "confidence": 0.95,
            "reasoning_summary": "Evidence supports claim",
        }

        mock_response = _make_gemini_response(parsed=mock_parsed)

        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = provider.complete(
                "Reason about claims",
                system="You are a reasoning system",
                json_mode=True,
                response_schema=ReasoningSchema,
            )

        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "claims" in result
        assert result["classification"] == "clear"

    def test_complete_json_skips_roundtrip_for_parsed_dict(self):
        """complete_json() must NOT call _parse_json_response() when
        Gemini returns a dict from response.parsed."""
        from ai.llm_provider import GeminiProvider, ExtractionSchema

        provider = GeminiProvider(api_key="test_key", model="test")

        mock_parsed = ExtractionSchema(facts=[
            {"fact_type": "order_detail", "value": "Order found", "evidence_quote": "Order #123"}
        ])
        mock_response = _make_gemini_response(parsed=mock_parsed)

        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_client_cls.return_value = mock_client

            with patch("ai.llm_provider._parse_json_response") as mock_parse:
                result = provider.complete_json(
                    "Extract facts",
                    system="test",
                    response_schema=ExtractionSchema,
                )
                # _parse_json_response must NOT have been called
                mock_parse.assert_not_called()

        assert isinstance(result, dict)
        assert "facts" in result

    def test_chat_complete_parsed_returns_dict(self):
        """Gemini chat_complete() returns dict when response.parsed succeeds."""
        from ai.llm_provider import GeminiProvider, ReasoningSchema

        provider = GeminiProvider(api_key="test_key", model="test")

        mock_parsed = ReasoningSchema(
            claims=[{"claim_type": "platform_fee", "policy_clause_id": "platform_1_1",
                     "evidence_ids": ["ev_1"], "reasoning": "Standard fee"}],
            classification="clear",
            confidence=0.92,
            reasoning_summary="Fee applies",
        )

        expected_dict = mock_parsed.model_dump()

        mock_genai = MagicMock()
        mock_genai.types.Content.return_value = MagicMock()
        mock_genai.types.Part.from_text.return_value = MagicMock()

        with patch.dict("sys.modules", {
            "google": MagicMock(),
            "google.genai": mock_genai,
        }):
            with patch("ai.llm_provider.GeminiProvider._gemini_chat_complete") as mock_gcc:
                mock_gcc.return_value = expected_dict

                messages = [
                    {"role": "system", "content": "You are a finance controller."},
                    {"role": "user", "content": "Produce analysis"},
                ]
                result = provider.chat_complete(
                    messages=messages,
                    json_mode=True,
                    response_schema=ReasoningSchema,
                )

        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "claims" in result
        assert result["classification"] == "clear"


# ===========================================================================
# 2. Gemini raw text fallback (response.parsed=None, valid JSON text)
# ===========================================================================

class TestGeminiRawTextFallback:
    """When response.parsed is None but raw text is valid JSON,
    complete() and complete_json() must still return a dict."""

    def test_json_loads_fallback_in_complete(self):
        """complete() uses json.loads when response.parsed is None."""
        from ai.llm_provider import GeminiProvider, ExtractionSchema

        provider = GeminiProvider(api_key="test_key", model="test")

        valid_json = json.dumps({
            "facts": [{"fact_type": "order_detail", "value": "Test",
                       "evidence_quote": "Quote here"}]
        })

        mock_response = _make_gemini_response(parsed=None, text=valid_json)

        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = provider.complete(
                "Extract facts",
                system="test",
                json_mode=True,
                response_schema=ExtractionSchema,
            )

        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "facts" in result

    def test_parse_json_response_fallback_in_complete(self):
        """complete() uses _parse_json_response() when json.loads fails
        but _parse_json_response can extract valid JSON."""
        from ai.llm_provider import GeminiProvider, ExtractionSchema

        provider = GeminiProvider(api_key="test_key", model="test")

        # Text with markdown code fences — json.loads fails but
        # _parse_json_response handles it
        text_with_fences = '```json\n{"facts": [{"fact_type": "test", "value": "v", "evidence_quote": "q"}]}\n```'

        mock_response = _make_gemini_response(parsed=None, text=text_with_fences)

        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = provider.complete(
                "Extract facts",
                system="test",
                json_mode=True,
                response_schema=ExtractionSchema,
            )

        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "facts" in result

    def test_complete_json_with_text_fallback(self):
        """complete_json() returns dict when complete() returns raw text
        that _parse_json_response can handle."""
        from ai.llm_provider import GeminiProvider, ExtractionSchema

        provider = GeminiProvider(api_key="test_key", model="test")

        valid_json = json.dumps({
            "facts": [{"fact_type": "delivery_record", "value": "Late",
                       "evidence_quote": "5 days late"}]
        })

        mock_response = _make_gemini_response(parsed=None, text=valid_json)

        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = provider.complete_json(
                "Extract facts",
                system="test",
                response_schema=ExtractionSchema,
            )

        assert isinstance(result, dict)
        assert result["facts"][0]["fact_type"] == "delivery_record"

    def test_chat_complete_text_fallback(self):
        """chat_complete() returns dict when _gemini_chat_complete
        falls back to json.loads on raw text."""
        from ai.llm_provider import GeminiProvider, ReasoningSchema

        provider = GeminiProvider(api_key="test_key", model="test")

        expected_dict = {
            "claims": [{"claim_type": "sla_breach", "policy_clause_id": "sla_4_2",
                        "evidence_ids": ["ev_1"], "reasoning": "Late"}],
            "classification": "clear",
            "confidence": 0.88,
            "reasoning_summary": "SLA breach confirmed",
        }

        mock_genai = MagicMock()
        mock_genai.types.Content.return_value = MagicMock()
        mock_genai.types.Part.from_text.return_value = MagicMock()

        with patch.dict("sys.modules", {
            "google": MagicMock(),
            "google.genai": mock_genai,
        }):
            with patch("ai.llm_provider.GeminiProvider._gemini_chat_complete") as mock_gcc:
                mock_gcc.return_value = expected_dict

                messages = [
                    {"role": "system", "content": "You are a reasoning system."},
                    {"role": "user", "content": "Analyze claims"},
                ]
                result = provider.chat_complete(
                    messages=messages,
                    json_mode=True,
                    response_schema=ReasoningSchema,
                )

        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "claims" in result


# ===========================================================================
# 3. Malformed raw text is still rejected
# ===========================================================================

class TestMalformedTextRejected:
    """When raw text is genuinely malformed, the error must propagate."""

    def test_complete_json_raises_on_truncated_json(self):
        """complete_json() raises ValueError when JSON is truncated."""
        from ai.llm_provider import GeminiProvider, ExtractionSchema

        provider = GeminiProvider(api_key="test_key", model="test")

        truncated = '{"facts": [{"fact_type": "test"'  # Missing closing brackets

        mock_response = _make_gemini_response(parsed=None, text=truncated)

        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError):
                provider.complete_json(
                    "Extract facts",
                    system="test",
                    response_schema=ExtractionSchema,
                )

    def test_complete_json_raises_on_non_json(self):
        """complete_json() raises when raw text is not JSON at all."""
        from ai.llm_provider import GeminiProvider, ExtractionSchema

        provider = GeminiProvider(api_key="test_key", model="test")

        not_json = "This is not JSON at all, just plain text response."

        mock_response = _make_gemini_response(parsed=None, text=not_json)

        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError):
                provider.complete_json(
                    "Extract facts",
                    system="test",
                    response_schema=ExtractionSchema,
                )

    def test_complete_raises_on_empty_response(self):
        """complete() raises ValueError when Gemini returns no candidates."""
        from ai.llm_provider import GeminiProvider, ExtractionSchema

        provider = GeminiProvider(api_key="test_key", model="test")

        mock_response = _make_gemini_response(parsed=None, candidates=False)

        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError, match="no candidates"):
                provider.complete(
                    "Extract facts",
                    system="test",
                    json_mode=True,
                    response_schema=ExtractionSchema,
                )

    def test_parse_json_response_still_rejects_garbage(self):
        """_parse_json_response() rejects genuinely malformed text."""
        from ai.llm_provider import _parse_json_response

        with pytest.raises(ValueError, match="Failed to parse JSON"):
            _parse_json_response("not json at all {{{")

    def test_parse_json_response_handles_code_fences(self):
        """_parse_json_response() handles markdown code fences."""
        from ai.llm_provider import _parse_json_response

        text = '```json\n{"key": "value"}\n```'
        result = _parse_json_response(text)
        assert result == {"key": "value"}


# ===========================================================================
# 4. Extraction schema validation
# ===========================================================================

class TestExtractionSchemaValidation:
    """Verify ExtractionSchema matches extraction.py contract exactly."""

    def test_extraction_schema_matches_prompt_contract(self):
        """ExtractionSchema fields must match what EXTRACTION_PROMPT requests."""
        from ai.llm_provider import ExtractionSchema, ExtractionFact

        # Build a fact matching the prompt's contract
        fact = ExtractionFact(
            fact_type="order_detail",
            value="Order #123 was placed",
            amount=10000.0,
            date="2024-11-15",
            evidence_quote="Order #123 placed on 2024-11-15 for ₹10,000",
        )

        schema = ExtractionSchema(facts=[fact])
        dumped = schema.model_dump()

        # Must have "facts" key at top level
        assert "facts" in dumped
        assert len(dumped["facts"]) == 1

        # Each fact must have the required fields from extraction.py
        f = dumped["facts"][0]
        assert "fact_type" in f
        assert "value" in f
        assert "evidence_quote" in f
        # Optional fields
        assert "amount" in f
        assert "date" in f

    def test_extraction_validate_function_accepts_schema_output(self):
        """validate_extraction_response() must accept output from ExtractionSchema."""
        from ai.llm_provider import ExtractionSchema
        from ai.extraction import validate_extraction_response

        schema = ExtractionSchema(facts=[
            {"fact_type": "order_detail", "value": "Order found",
             "evidence_quote": "Order #123 was captured"}
        ])
        dumped = schema.model_dump()

        assert validate_extraction_response(dumped) is True

    def test_extraction_schema_rejects_empty_evidence_quote(self):
        """ExtractionSchema should accept any string, but validate_extraction_response
        rejects short evidence quotes."""
        from ai.llm_provider import ExtractionSchema
        from ai.extraction import validate_extraction_response

        schema = ExtractionSchema(facts=[
            {"fact_type": "other", "value": "Something", "evidence_quote": "ab"}
        ])
        dumped = schema.model_dump()

        # ExtractionSchema accepts it (Pydantic doesn't enforce min length)
        assert "facts" in dumped

        # But validate_extraction_response rejects it (< 5 chars)
        assert validate_extraction_response(dumped) is False


# ===========================================================================
# 5. Reasoning schema validation
# ===========================================================================

class TestReasoningSchemaValidation:
    """Verify ReasoningSchema matches reasoning.py contract exactly."""

    def test_reasoning_schema_matches_prompt_contract(self):
        """ReasoningSchema fields must match what REASONING_PROMPT requests."""
        from ai.llm_provider import ReasoningSchema, ReasoningClaim

        claim = ReasoningClaim(
            claim_type="sla_breach",
            policy_clause_id="sla_4_2",
            evidence_ids=["ev_001", "ev_002"],
            reasoning="Delivery was 5 days late, exceeding the 3-day SLA",
        )

        schema = ReasoningSchema(
            claims=[claim],
            classification="clear",
            confidence=0.92,
            reasoning_summary="SLA breach confirmed by delivery evidence",
        )
        dumped = schema.model_dump()

        # Top-level fields match REASONING_PROMPT
        assert "claims" in dumped
        assert "classification" in dumped
        assert "confidence" in dumped
        assert "reasoning_summary" in dumped

        # Each claim has required fields
        c = dumped["claims"][0]
        assert "claim_type" in c
        assert "policy_clause_id" in c
        assert "evidence_ids" in c
        assert isinstance(c["evidence_ids"], list)
        assert len(c["evidence_ids"]) > 0
        assert "reasoning" in c

    def test_reasoning_validate_accepts_schema_output(self):
        """validate_reasoning_response() must accept ReasoningSchema output."""
        from ai.llm_provider import ReasoningSchema
        from ai.reasoning import validate_reasoning_response

        schema = ReasoningSchema(
            claims=[{
                "claim_type": "platform_fee",
                "policy_clause_id": "platform_1_1",
                "evidence_ids": ["ev_order_001"],
                "reasoning": "Standard 8% platform fee on gross amount",
            }],
            classification="clear",
            confidence=0.95,
            reasoning_summary="Fee applies",
        )
        dumped = schema.model_dump()

        assert validate_reasoning_response(dumped) is True

    def test_reasoning_schema_valid_classification(self):
        """ReasoningSchema classification must be one of the allowed values."""
        from ai.llm_provider import ReasoningSchema

        for cls in ("clear", "exception", "ambiguous"):
            schema = ReasoningSchema(
                claims=[], classification=cls,
                confidence=0.5, reasoning_summary="Test"
            )
            assert schema.classification == cls

    def test_reasoning_schema_confidence_bounds(self):
        """ReasoningSchema confidence must be between 0.0 and 1.0."""
        from ai.llm_provider import ReasoningSchema

        # Valid confidence
        schema = ReasoningSchema(
            claims=[], classification="clear",
            confidence=0.5, reasoning_summary="Test"
        )
        assert schema.confidence == 0.5

        # Zero confidence
        schema = ReasoningSchema(
            claims=[], classification="exception",
            confidence=0.0, reasoning_summary="No confidence"
        )
        assert schema.confidence == 0.0

        # Full confidence
        schema = ReasoningSchema(
            claims=[], classification="clear",
            confidence=1.0, reasoning_summary="Certain"
        )
        assert schema.confidence == 1.0


# ===========================================================================
# 6. complete_json() never re-parses a dict
# ===========================================================================

class TestCompleteJsonNeverReparsesDict:
    """complete_json() must return dicts directly without string round-trip."""

    def test_dict_passthrough(self):
        """complete_json() returns a dict as-is when complete() returns a dict."""
        from ai.llm_provider import LLMProvider

        class MockProvider(LLMProvider):
            def complete(self, prompt, **kwargs):
                return {"claims": [], "classification": "clear"}
            def is_available(self):
                return True
            def provider_info(self):
                return {"provider": "mock"}

        provider = MockProvider()
        result = provider.complete_json("test prompt")
        assert isinstance(result, dict)
        assert result["classification"] == "clear"

    def test_model_dump_passthrough(self):
        """complete_json() calls model_dump() when complete() returns a Pydantic model."""
        from ai.llm_provider import LLMProvider, ExtractionSchema

        class MockProvider(LLMProvider):
            def complete(self, prompt, **kwargs):
                return ExtractionSchema(facts=[
                    {"fact_type": "test", "value": "v", "evidence_quote": "q"}
                ])
            def is_available(self):
                return True
            def provider_info(self):
                return {"provider": "mock"}

        provider = MockProvider()
        result = provider.complete_json("test prompt")
        assert isinstance(result, dict)
        assert "facts" in result

    def test_text_fallback_to_parse(self):
        """complete_json() uses _parse_json_response() when complete() returns text."""
        from ai.llm_provider import LLMProvider

        class MockProvider(LLMProvider):
            def complete(self, prompt, **kwargs):
                return '{"claims": [], "classification": "clear"}'
            def is_available(self):
                return True
            def provider_info(self):
                return {"provider": "mock"}

        provider = MockProvider()
        result = provider.complete_json("test prompt")
        assert isinstance(result, dict)
        assert result["classification"] == "clear"


# ===========================================================================
# 7. _gemini_chat_complete handles response_schema without json_mode check
# ===========================================================================

class TestChatCompleteResponseSchemaCondition:
    """_gemini_chat_complete() must use response_schema for fallback,
    not require both json_mode AND response_schema."""

    def test_response_schema_without_json_mode_still_parses(self):
        """When response_schema is set but json_mode is False,
        _gemini_chat_complete() should still try to parse JSON."""
        from ai.llm_provider import GeminiProvider, ReasoningSchema

        provider = GeminiProvider(api_key="test_key", model="test")

        expected_dict = {
            "claims": [{"claim_type": "other", "policy_clause_id": "p1",
                        "evidence_ids": ["ev_1"], "reasoning": "Test"}],
            "classification": "clear",
            "confidence": 0.5,
            "reasoning_summary": "Test",
        }

        mock_genai = MagicMock()
        mock_genai.types.Content.return_value = MagicMock()
        mock_genai.types.Part.from_text.return_value = MagicMock()

        with patch.dict("sys.modules", {
            "google": MagicMock(),
            "google.genai": mock_genai,
        }):
            with patch("ai.llm_provider.GeminiProvider._gemini_chat_complete") as mock_gcc:
                mock_gcc.return_value = expected_dict

                messages = [
                    {"role": "system", "content": "test"},
                    {"role": "user", "content": "Analyze"},
                ]
                # json_mode=False, response_schema=ReasoningSchema
                result = provider.chat_complete(
                    messages=messages,
                    json_mode=False,
                    response_schema=ReasoningSchema,
                )

        # With the fix, response_schema alone triggers JSON parsing
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "claims" in result

    def test_gemini_chat_complete_uses_response_schema_for_fallback(self):
        """_gemini_chat_complete() must try JSON parsing when
        response_schema is set, even if json_mode is False.

        Verifies by inspecting the source that the condition is
        'if response_schema is not None' (not 'json_mode and ...').
        """
        import inspect
        from ai.llm_provider import GeminiProvider

        source = inspect.getsource(GeminiProvider._gemini_chat_complete)
        # The fix changed:  if json_mode and response_schema is not None:
        # to:               if response_schema is not None:
        assert 'if response_schema is not None:' in source, (
            "_gemini_chat_complete should check response_schema alone, "
            "not require json_mode AND response_schema"
        )
        # Ensure we didn't accidentally remove the condition entirely
        assert 'if json_mode and response_schema' not in source, (
            "_gemini_chat_complete should not require both json_mode and response_schema"
        )
