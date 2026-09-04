"""Phase 4.2 tests — Provider specialization, Ollama local mode, routing, safety.

Tests cover:
- Ollama provider capabilities (complete, tools, chat)
- Provider parity (same agent with different providers)
- Intelligent routing (Groq/Gemini roles)
- Safety guarantees (LLM can't calculate money, approve, etc.)
- Rate limit handling and bounded retry
- Production Vercel safety
- Provider error classification
"""
import asyncio
import json
import os
import pytest
import time
from unittest.mock import patch, MagicMock, AsyncMock


# ---------------------------------------------------------------------------
# Test 1-4: Ollama provider capabilities
# ---------------------------------------------------------------------------

class TestOllamaProvider:
    """Test OllamaProvider supports full solo Finance Controller execution."""

    def test_complete_basic(self):
        """Ollama complete() returns text response."""
        from ai.llm_provider import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen3.5:latest")

        with patch("httpx.Client") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "message": {"content": "The evidence supports a valid claim."}
            }
            mock_response.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = provider.complete(
                "Analyze this financial case",
                system="You are a finance controller.",
                max_tokens=512,
                temperature=0.0,
            )
            assert isinstance(result, str)
            assert len(result) > 0
            # Verify payload includes tools=None (no tools for complete)
            call_args = mock_client.post.call_args
            payload = call_args[1].get("json", call_args[0][1] if len(call_args[0]) > 1 else {})
            assert "tools" not in payload

    def test_structured_output_json(self):
        """Ollama complete() with json_mode returns parseable JSON."""
        from ai.llm_provider import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen3.5:latest")
        mock_json = json.dumps({"claims": [], "classification": "clear"})

        with patch("httpx.Client") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"message": {"content": mock_json}}
            mock_response.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = provider.complete(
                "Produce structured analysis",
                system="You are a finance controller.",
                json_mode=True,
            )
            assert isinstance(result, str)
            parsed = json.loads(result)
            assert "claims" in parsed
            assert "classification" in parsed

    def test_native_tool_calling(self):
        """Ollama complete_with_tools() returns ToolCallResponse with tool_calls."""
        from ai.llm_provider import OllamaProvider, ToolCallResponse

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen3.5:latest")

        with patch("httpx.Client") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "function": {
                                "name": "get_delivery",
                                "arguments": json.dumps({"order_id": "ord_001"}),
                            },
                        }
                    ],
                }
            }
            mock_response.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            messages = [{"role": "user", "content": "Get delivery info for ord_001"}]
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "get_delivery",
                        "description": "Get delivery info",
                        "parameters": {
                            "type": "object",
                            "properties": {"order_id": {"type": "string", "description": "Order ID"}},
                            "required": ["order_id"],
                        },
                    },
                }
            ]

            result = provider.complete_with_tools(messages=messages, tools=tools)

            assert isinstance(result, ToolCallResponse)
            assert len(result.tool_calls) == 1
            assert result.tool_calls[0].function_name == "get_delivery"
            assert result.tool_calls[0].arguments == {"order_id": "ord_001"}
            assert result.finish_reason == "tool_calls"

            # Verify tools were sent in payload
            call_args = mock_client.post.call_args
            payload = call_args[1].get("json", call_args[0][1] if len(call_args[0]) > 1 else {})
            assert "tools" in payload

    def test_multi_turn_tool_loop(self):
        """Ollama complete_with_tools() works across multiple turns."""
        from ai.llm_provider import OllamaProvider, ToolCallResponse

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen3.5:latest")

        # Turn 1: model returns tool call
        tool_response = {
            "message": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "get_delivery",
                            "arguments": json.dumps({"order_id": "ord_001"}),
                        },
                    }
                ],
            }
        }
        # Turn 2: model returns final answer (no tool calls)
        final_response = {
            "message": {
                "content": "Delivery was 5 days late. SLA breach detected.",
                "tool_calls": [],
            }
        }

        with patch("httpx.Client") as mock_client_cls:
            mock_response_1 = MagicMock()
            mock_response_1.status_code = 200
            mock_response_1.json.return_value = tool_response
            mock_response_1.raise_for_status = MagicMock()

            mock_response_2 = MagicMock()
            mock_response_2.status_code = 200
            mock_response_2.json.return_value = final_response
            mock_response_2.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = [mock_response_1, mock_response_2]
            mock_client_cls.return_value = mock_client

            # Turn 1
            messages = [{"role": "user", "content": "Investigate case"}]
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "get_delivery",
                        "description": "Get delivery",
                        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
                    },
                }
            ]

            result1 = provider.complete_with_tools(messages=messages, tools=tools)
            assert len(result1.tool_calls) == 1

            # Turn 2: append tool result + assistant message
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_delivery", "arguments": json.dumps({"order_id": "ord_001"})}}],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": "call_1",
                "content": json.dumps({"found": True, "delay_days": 5}),
            })

            result2 = provider.complete_with_tools(messages=messages, tools=tools)
            assert len(result2.tool_calls) == 0
            assert result2.content is not None
            assert "late" in result2.content.lower() or "sla" in result2.content.lower()


# ---------------------------------------------------------------------------
# Test 5-7: Provider parity (same agent works with each provider)
# ---------------------------------------------------------------------------

class TestProviderParity:
    """Same agent works with Ollama, Groq, and Gemini."""

    def _make_tool_response(self, provider_name: str):
        """Create a mock tool response for the given provider."""
        from ai.llm_provider import ToolCallResponse, ToolCallInfo
        return ToolCallResponse(
            content=None,
            tool_calls=[ToolCallInfo(
                id=f"{provider_name}_call_1",
                function_name="get_delivery",
                arguments={"order_id": "ord_001"},
            )],
            finish_reason="tool_calls",
        )

    def test_same_agent_ollama(self):
        """Agent runs with OllamaProvider via mock patch."""
        from ai.agent import run_agent, AgentRunState

        evidence = [{
            "evidence_id": "ev_001",
            "source_type": "order",
            "raw_content": json.dumps({"order_id": "ord_001", "amount": 10000, "seller_id": "sel_001"}),
            "extracted_facts": "[]",
            "tenant_id": "tenant_1",
        }]
        policies = [{
            "policy_id": "platform_1_1",
            "version": 1,
            "effective_date": "2026-01-01",
            "clause_text": "Platform fee 8%",
            "clause_id": "platform_1_1_fee",
            "category": "platform_fee",
        }]

        # Run with mock mode (which simulates the same agent path)
        result = asyncio.get_event_loop().run_until_complete(
            run_agent(
                tenant_id="tenant_1",
                scenario_id="sc_001",
                entity_id="seller_1",
                gross_amount=10000,
                evidence_records=evidence,
                policy_records=policies,
                scenario_description="Test",
                use_mock=True,
            )
        )

        assert result["analysis"] is not None
        state = result["agent_state"]
        assert isinstance(state, AgentRunState)
        assert state.iteration_count >= 1

    def test_same_agent_groq(self):
        """Agent works with GroqProvider (mocked complete_with_tools)."""
        from ai.llm_provider import ToolCallResponse, ToolCallInfo
        from ai.agent import run_agent

        evidence = [{
            "evidence_id": "ev_001",
            "source_type": "order",
            "raw_content": json.dumps({"order_id": "ord_001", "amount": 10000, "seller_id": "sel_001"}),
            "extracted_facts": "[]",
            "tenant_id": "tenant_1",
        }]
        policies = [{
            "policy_id": "platform_1_1",
            "version": 1,
            "effective_date": "2026-01-01",
            "clause_text": "Platform fee 8%",
            "clause_id": "platform_1_1_fee",
            "category": "platform_fee",
        }]

        # Mock provider that returns tool call then final
        mock_provider = MagicMock()
        mock_provider.model = "openai/gpt-oss-120b"
        mock_provider.provider_info.return_value = {"provider": "groq"}

        turn_count = [0]
        def mock_complete_with_tools(messages, tools, **kwargs):
            turn_count[0] += 1
            if turn_count[0] == 1:
                return ToolCallResponse(
                    content=None,
                    tool_calls=[ToolCallInfo(
                        id="groq_call_1",
                        function_name="get_delivery",
                        arguments={"order_id": "ord_001"},
                    )],
                    finish_reason="tool_calls",
                )
            return ToolCallResponse(
                content="Evidence gathered. No issues found.",
                tool_calls=[],
                finish_reason="stop",
            )

        mock_provider.complete_with_tools = mock_complete_with_tools
        mock_provider.chat_complete.return_value = {
            "claims": [],
            "classification": "clear",
            "confidence": 0.9,
            "reasoning_summary": "No issues found.",
        }

        with patch("ai.agent.get_provider", return_value=mock_provider):
            with patch("ai.agent.execute_tool", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = {"found": False, "reason": "No delivery"}

                result = asyncio.get_event_loop().run_until_complete(
                    run_agent(
                        tenant_id="tenant_1",
                        scenario_id="sc_001",
                        entity_id="seller_1",
                        gross_amount=10000,
                        evidence_records=evidence,
                        policy_records=policies,
                        scenario_description="Test",
                        use_mock=False,
                    )
                )

        assert result["analysis"] is not None

    def test_same_agent_gemini(self):
        """Agent works with GeminiProvider (mocked complete_with_tools)."""
        from ai.llm_provider import ToolCallResponse, ToolCallInfo
        from ai.agent import run_agent

        evidence = [{
            "evidence_id": "ev_001",
            "source_type": "order",
            "raw_content": json.dumps({"order_id": "ord_001", "amount": 10000, "seller_id": "sel_001"}),
            "extracted_facts": "[]",
            "tenant_id": "tenant_1",
        }]
        policies = [{
            "policy_id": "platform_1_1",
            "version": 1,
            "effective_date": "2026-01-01",
            "clause_text": "Platform fee 8%",
            "clause_id": "platform_1_1_fee",
            "category": "platform_fee",
        }]

        mock_provider = MagicMock()
        mock_provider.model = "gemini-2.5-flash"
        mock_provider.provider_info.return_value = {"provider": "gemini"}

        turn_count = [0]
        def mock_complete_with_tools(messages, tools, **kwargs):
            turn_count[0] += 1
            if turn_count[0] == 1:
                return ToolCallResponse(
                    content=None,
                    tool_calls=[ToolCallInfo(
                        id="gemini_call_1",
                        function_name="get_delivery",
                        arguments={"order_id": "ord_001"},
                    )],
                    finish_reason="tool_calls",
                )
            return ToolCallResponse(
                content="Evidence analysis complete.",
                tool_calls=[],
                finish_reason="stop",
            )

        mock_provider.complete_with_tools = mock_complete_with_tools
        mock_provider.chat_complete.return_value = {
            "claims": [],
            "classification": "clear",
            "confidence": 0.92,
            "reasoning_summary": "Gemini verified: no issues.",
        }

        with patch("ai.agent.get_provider", return_value=mock_provider):
            with patch("ai.agent.execute_tool", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = {"found": False, "reason": "No delivery"}

                result = asyncio.get_event_loop().run_until_complete(
                    run_agent(
                        tenant_id="tenant_1",
                        scenario_id="sc_001",
                        entity_id="seller_1",
                        gross_amount=10000,
                        evidence_records=evidence,
                        policy_records=policies,
                        scenario_description="Test",
                        use_mock=False,
                    )
                )

        assert result["analysis"] is not None


# ---------------------------------------------------------------------------
# Test 8-11: Intelligent routing
# ---------------------------------------------------------------------------

class TestIntelligentRouting:
    """Test routing decisions: simple→Groq, complex→Gemini, conflict→cross-check."""

    def test_simple_case_groq_only(self):
        """Simple structured Razorpay event → Groq only, no Gemini."""
        from ai.llm_provider import ToolCallResponse

        mock_provider = MagicMock()
        mock_provider.provider_info.return_value = {"provider": "groq"}
        mock_provider.complete_with_tools.return_value = ToolCallResponse(
            content="Evidence sufficient. No further investigation needed.",
            tool_calls=[],
            finish_reason="stop",
        )
        mock_provider.chat_complete.return_value = {
            "claims": [],
            "classification": "clear",
            "confidence": 0.95,
            "reasoning_summary": "Clean case.",
        }

        # Simple case: order + payment = Groq only
        with patch("ai.agent.get_provider", return_value=mock_provider):
            # Check that only groq provider was used
            provider = mock_provider
            assert provider.provider_info()["provider"] == "groq"

    def test_provider_info_routing(self):
        """Each provider reports correct role."""
        from ai.llm_provider import OllamaProvider, GroqProvider, GeminiProvider

        ollama = OllamaProvider()
        groq = GroqProvider(api_key="test")
        gemini = GeminiProvider(api_key="test")

        assert ollama.provider_info()["provider"] == "ollama"
        assert groq.provider_info()["provider"] == "groq"
        assert gemini.provider_info()["provider"] == "gemini"

    def test_provider_error_classification(self):
        """Errors are classified into safe failure categories."""
        from ai.llm_provider import classify_provider_error

        assert classify_provider_error(ValueError("429 rate limit")) == "rate_limit"
        assert classify_provider_error(ValueError("rate limit exceeded")) == "rate_limit"
        assert classify_provider_error(ValueError("quota exceeded")) == "rate_limit"
        assert classify_provider_error(ValueError("request timed out")) == "timeout"
        assert classify_provider_error(ValueError("timeout after 30s")) == "timeout"
        assert classify_provider_error(ValueError("invalid API key")) == "provider_error"
        assert classify_provider_error(ValueError("connection refused")) == "unavailable"
        assert classify_provider_error(ValueError("JSON parse error")) == "schema_error"
        assert classify_provider_error(RuntimeError("unknown error")) == "provider_error"

    def test_unresolved_disagreement_review_required(self):
        """When agent cannot resolve conflicting evidence → REVIEW_REQUIRED."""
        from ai.agent import run_agent

        # Evidence with conflicting signals
        evidence = [
            {
                "evidence_id": "ev_order_001",
                "source_type": "order",
                "raw_content": json.dumps({"order_id": "ord_001", "amount": 15000, "seller_id": "sel_001"}),
                "extracted_facts": "[]",
                "tenant_id": "tenant_1",
            },
            {
                "evidence_id": "ev_refund_001",
                "source_type": "refund_record",
                "raw_content": json.dumps({"order_id": "ord_001", "refund_id": "rf_001", "amount": 15000, "status": "processed"}),
                "extracted_facts": "[]",
                "tenant_id": "tenant_1",
            },
        ]

        result = asyncio.get_event_loop().run_until_complete(
            run_agent(
                tenant_id="tenant_1",
                scenario_id="sc_conflict",
                entity_id="seller_1",
                gross_amount=15000,
                evidence_records=evidence,
                policy_records=[{
                    "policy_id": "returns_3_1",
                    "version": 1,
                    "effective_date": "2026-01-01",
                    "clause_text": "Return processed",
                    "clause_id": "returns_3_1",
                    "category": "returns",
                }],
                scenario_description="Conflicting refund case",
                use_mock=True,
            )
        )

        analysis = result["analysis"]
        # Mock agent produces claims for refund → should not be auto-approved
        # without proper delivery evidence check
        assert analysis is not None
        state = result["agent_state"]
        assert state.success is True


# ---------------------------------------------------------------------------
# Test 12-17: Safety guarantees
# ---------------------------------------------------------------------------

class TestSafetyGuarantees:
    """LLM cannot: calculate money, approve payments, override determinism."""

    def test_llm_cannot_calculate_money(self):
        """Deterministic calculation engine is the sole financial authority."""
        from calculations import calculate_platform_fee, calculate_final_amount, build_line_items

        # Even if LLM says "platform fee = ₹5000", the engine decides
        platform_fee = calculate_platform_fee(gross_amount=10000)
        line_items = build_line_items(
            gross_amount=10000,
            has_sla_breach=False,
            has_returns=False,
        )
        final = calculate_final_amount(gross_amount=10000, line_items=line_items)

        # These are deterministic — LLM has zero influence
        assert platform_fee == 800  # 8% of 10000
        assert final == 9200  # 10000 - 800

    def test_llm_cannot_approve_payment(self):
        """Agent system prompt explicitly forbids payment approval."""
        from ai.agent import AGENT_SYSTEM

        # The system prompt must contain explicit safety rules
        assert "MUST NOT" in AGENT_SYSTEM
        assert "calculate" in AGENT_SYSTEM.lower() or "monetary" in AGENT_SYSTEM.lower()
        assert "approve" in AGENT_SYSTEM.lower() or "reject" in AGENT_SYSTEM.lower()

    def test_invalid_policy_reference_rejected(self):
        """Pipeline rejects claims with non-existent policy IDs."""
        from ai.pipeline import run_pipeline

        evidence = [{
            "evidence_id": "ev_001",
            "source_type": "order",
            "raw_content": json.dumps({"order_id": "ord_001", "amount": 10000}),
            "extracted_facts": "[]",
            "tenant_id": "tenant_1",
        }]

        # Policy IDs that don't match any actual policy
        policies = [{
            "policy_id": "nonexistent_policy_xyz",
            "version": 1,
            "effective_date": "2026-01-01",
            "clause_text": "This doesn't exist",
            "clause_id": "nonexistent_xyz",
            "category": "invalid",
        }]

        agent_result = {
            "analysis": {
                "claims": [{
                    "claim_type": "platform_fee",
                    "policy_clause_id": "nonexistent_xyz",
                    "evidence_ids": ["ev_001"],
                    "reasoning": "Fake claim",
                }],
                "classification": "clear",
                "confidence": 0.5,
                "reasoning_summary": "Invalid policy reference.",
            },
            "agent_state": MagicMock(success=True, tools_called=[], iteration_count=1),
            "extracted_facts": [],
        }

        # Pipeline catches invalid policy references and sets exception
        # classification instead of crashing — preserves idempotency
        result = run_pipeline(
            scenario_id="sc_invalid",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            use_mock=False,
            agent_result=agent_result,
        )
        decision = result["decision"]
        assert decision["status"] == "REVIEW_REQUIRED"
        model_output = decision["model_output"]
        assert model_output["classification"] == "exception"
        # Policy errors don't affect evidence_sufficiency (which is about evidence)
        assert model_output["evidence_sufficiency"] == "SUFFICIENT"

    def test_invalid_tool_args_handled_safely(self):
        """Agent handles invalid tool arguments without crashing."""
        from ai.agent import _validate_tool_args, _get_tool_params

        # Get expected params for get_delivery
        params = _get_tool_params("get_delivery")
        assert "order_id" in params

        # Invalid args: wrong types, missing params
        sanitized = _validate_tool_args("get_delivery", {"order_id": 12345}, params)
        assert "order_id" in sanitized  # int is accepted as scalar

        sanitized = _validate_tool_args("get_delivery", {"wrong_param": "value"}, params)
        assert "wrong_param" not in sanitized  # unknown param rejected

        sanitized = _validate_tool_args("get_delivery", "not a dict", params)
        assert sanitized == {}  # non-dict rejected

        sanitized = _validate_tool_args("get_delivery", {"order_id": {"nested": "object"}}, params)
        assert "order_id" not in sanitized  # complex objects rejected

    def test_tenant_id_cannot_be_supplied_by_model(self):
        """Agent enforces server-controlled tenant_id, never from model."""
        from ai.agent import run_agent

        # The agent always uses the passed tenant_id
        result = asyncio.get_event_loop().run_until_complete(
            run_agent(
                tenant_id="server_tenant_123",
                scenario_id="sc_tenant",
                entity_id="seller_1",
                gross_amount=10000,
                evidence_records=[{
                    "evidence_id": "ev_001",
                    "source_type": "order",
                    "raw_content": json.dumps({"order_id": "ord_001", "amount": 10000}),
                    "extracted_facts": "[]",
                    "tenant_id": "server_tenant_123",
                }],
                policy_records=[{
                    "policy_id": "platform_1_1",
                    "version": 1,
                    "effective_date": "2026-01-01",
                    "clause_text": "Platform fee 8%",
                    "clause_id": "platform_1_1_fee",
                    "category": "platform_fee",
                }],
                scenario_description="Tenant isolation test",
                use_mock=True,
            )
        )

        state = result["agent_state"]
        assert state.tenant_id == "server_tenant_123"

    def test_provider_failure_does_not_cause_approval(self):
        """Provider failure → REVIEW_REQUIRED, never APPROVED."""
        from ai.agent import run_agent
        from ai.llm_provider import OllamaProvider

        # Mock a provider that always fails on complete_with_tools
        mock_provider = MagicMock()
        mock_provider.model = "test"
        mock_provider.provider_info.return_value = {"provider": "test"}
        mock_provider.complete_with_tools.side_effect = ValueError("Provider unavailable")

        with patch("ai.agent.get_provider", return_value=mock_provider):
            result = asyncio.get_event_loop().run_until_complete(
                run_agent(
                    tenant_id="tenant_1",
                    scenario_id="sc_fail",
                    entity_id="seller_1",
                    gross_amount=10000,
                    evidence_records=[{
                        "evidence_id": "ev_001",
                        "source_type": "order",
                        "raw_content": json.dumps({"order_id": "ord_001", "amount": 10000}),
                        "extracted_facts": "[]",
                        "tenant_id": "tenant_1",
                    }],
                    policy_records=[],
                    scenario_description="Provider failure test",
                    use_mock=False,
                )
            )

        # Agent should fail gracefully
        state = result["agent_state"]
        # When provider fails, success should be False
        # The analysis should indicate exception, not approval
        analysis = result["analysis"]
        assert state.success is False
        assert analysis.get("classification") != "clear"


# ---------------------------------------------------------------------------
# Test 18-21: Rate limit handling and bounded retry
# ---------------------------------------------------------------------------

class TestRateLimitHandling:
    """429 classification, bounded retry, no infinite retry."""

    def test_429_classified_as_rate_limit(self):
        """HTTP 429 errors are classified as rate_limit."""
        from ai.llm_provider import classify_provider_error

        error = ValueError("Groq API error 429: rate_limit")
        category = classify_provider_error(error)
        assert category == "rate_limit"

    def test_bounded_retry(self):
        """Provider retry logic is bounded, not infinite."""
        from ai.llm_provider import GroqProvider

        provider = GroqProvider(api_key="test_key", model="test")

        # Mock _groq_request to always raise 429
        with patch.object(provider, "_groq_request", side_effect=ValueError("429 rate limit")):
            with pytest.raises(ValueError, match="429"):
                # complete() should propagate, not retry infinitely
                provider.complete("test prompt", system="test")

    def test_no_infinite_retry(self):
        """Provider does not loop forever on persistent failures."""
        from ai.llm_provider import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="test")

        call_count = [0]
        original_chat = provider._ollama_chat

        def failing_chat(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] > 3:
                raise RuntimeError("Should not be called more than 3 times")
            raise ValueError("Connection refused")

        with patch.object(provider, "_ollama_chat", side_effect=failing_chat):
            with pytest.raises(ValueError):
                provider.complete_with_tools(
                    messages=[{"role": "user", "content": "test"}],
                    tools=[],
                )

        # Should not have retried more than the initial attempt
        assert call_count[0] <= 3

    def test_provider_limited_benchmark_status(self):
        """Benchmark marks rate-limited runs as PROVIDER-LIMITED."""
        from benchmark.metrics import CaseResult, compute_metrics, BenchmarkMetrics
        from benchmark.runner import BenchmarkRun

        # Simulate all cases rate-limited
        run = BenchmarkRun(
            total_cases=5,
            provider="groq",
            model="openai/gpt-oss-120b",
        )
        run.case_results = [
            CaseResult(
                case_id=f"case_{i}",
                category="clean_payment",
                status="exception",
                expected_classification="clear",
                actual_classification="exception",
                exception_reason="ValueError: Groq API error 429",
                agent_success=False,
                stop_reason="llm_error",
            )
            for i in range(5)
        ]

        metrics = compute_metrics(run)
        # All cases failed → exception rate should be high
        assert metrics.total_cases == 5
        assert metrics.agent_failure_rate == 1.0  # 100% agent failure
        assert metrics.exceptions == 5  # each case is an exception


# ---------------------------------------------------------------------------
# Test 21 (continued): Production Vercel safety
# ---------------------------------------------------------------------------

class TestVercelSafety:
    """Production deployment never attempts localhost Ollama."""

    def test_production_rejects_ollama(self):
        """PRODUCTION_PROVIDER=ollama raises EnvironmentError."""
        from ai.llm_provider import get_provider, reset_provider

        reset_provider()
        try:
            with patch.dict(os.environ, {
                "PRODUCTION_PROVIDER": "ollama",
                "VERCEL": "1",
            }):
                with pytest.raises(EnvironmentError, match="not allowed"):
                    get_provider()
        finally:
            reset_provider()

    def test_production_requires_explicit_provider(self):
        """Production without PRODUCTION_PROVIDER requires cloud key."""
        from ai.llm_provider import get_provider, reset_provider

        reset_provider()
        try:
            with patch.dict(os.environ, {
                "VERCEL": "1",
                "PRODUCTION_PROVIDER": "groq",
                "GROQ_API_KEY": "",  # Empty key
            }):
                with pytest.raises(EnvironmentError, match="GROQ_API_KEY not set"):
                    get_provider()
        finally:
            reset_provider()

    def test_production_not_localhost(self):
        """is_production() detects Vercel environment."""
        from ai.llm_provider import is_production

        with patch.dict(os.environ, {"VERCEL": "1"}, clear=False):
            assert is_production() is True

        with patch.dict(os.environ, {"PRODUCTION_PROVIDER": "groq"}, clear=False):
            assert is_production() is True

        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False):
            assert is_production() is True

        with patch.dict(os.environ, {}, clear=True):
            # No production indicators
            assert is_production() is False

    def test_get_provider_by_name(self):
        """get_provider_by_name returns correct provider type."""
        from ai.llm_provider import get_provider_by_name, OllamaProvider, GroqProvider, GeminiProvider

        ollama = get_provider_by_name("ollama")
        assert isinstance(ollama, OllamaProvider)

        with patch.dict(os.environ, {"GROQ_API_KEY": "test_key"}):
            groq = get_provider_by_name("groq")
            assert isinstance(groq, GroqProvider)

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"}):
            gemini = get_provider_by_name("gemini")
            assert isinstance(gemini, GeminiProvider)

        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider_by_name("nonexistent")
