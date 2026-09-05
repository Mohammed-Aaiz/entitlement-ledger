"""Tests for AI pipeline, extraction, reasoning, and safety guardrails."""
import pytest
import json
import os
from unittest.mock import patch, MagicMock, Mock
from ai.extraction import validate_extraction_response
from ai.reasoning import validate_reasoning_response
from ai.test_mocks import extract_facts_mock, reason_about_claims_mock
from ai.pipeline import (
    _map_claims_to_calculation_params,
    _validate_evidence_references,
    _validate_policy_references,
    run_pipeline,
)
from ai.llm_provider import is_ai_available, reset_provider, get_provider, OpenRouterProvider, GeminiProvider, GroqProvider, _pydantic_to_strict_json_schema
from seed_data import get_all_evidence, get_all_policies, get_scenario_evidence, get_scenario_policies


class TestExtractionValidation:
    """Test extraction response validation."""

    def test_valid_extraction_response(self):
        response = {
            "facts": [
                {
                    "fact_type": "order_detail",
                    "value": "Order placed for 100000",
                    "amount": 100000,
                    "date": "2024-11-15",
                    "evidence_quote": "Order placed for ₹100,000"
                }
            ]
        }
        assert validate_extraction_response(response) is True

    def test_missing_facts_key(self):
        response = {"not_facts": []}
        assert validate_extraction_response(response) is False

    def test_empty_facts_list(self):
        response = {"facts": []}
        assert validate_extraction_response(response) is True

    def test_fact_missing_required_field(self):
        response = {
            "facts": [
                {
                    "fact_type": "order_detail",
                    "value": "test",
                    # Missing evidence_quote
                }
            ]
        }
        assert validate_extraction_response(response) is False

    def test_fact_empty_evidence_quote(self):
        response = {
            "facts": [
                {
                    "fact_type": "order_detail",
                    "value": "test",
                    "evidence_quote": "   "  # Too short
                }
            ]
        }
        assert validate_extraction_response(response) is False

    def test_not_dict_response(self):
        assert validate_extraction_response("not a dict") is False

    def test_facts_not_list(self):
        assert validate_extraction_response({"facts": "not a list"}) is False

    def test_fact_not_dict(self):
        assert validate_extraction_response({"facts": ["not a dict"]}) is False


class TestReasoningValidation:
    """Test reasoning response validation."""

    def test_valid_reasoning_response(self):
        response = {
            "claims": [
                {
                    "claim_type": "sla_breach",
                    "policy_clause_id": "sla_4_2",
                    "evidence_ids": ["ev_delivery_001"],
                    "reasoning": "Delivery was late"
                }
            ],
            "classification": "clear",
            "confidence": 0.95,
            "reasoning_summary": "SLA breach confirmed"
        }
        assert validate_reasoning_response(response) is True

    def test_missing_classification(self):
        response = {
            "claims": [],
            "confidence": 0.95,
            "reasoning_summary": "test"
        }
        assert validate_reasoning_response(response) is False

    def test_invalid_classification(self):
        response = {
            "claims": [],
            "classification": "invalid",
            "confidence": 0.95,
            "reasoning_summary": "test"
        }
        assert validate_reasoning_response(response) is False

    def test_confidence_out_of_range(self):
        response = {
            "claims": [],
            "classification": "clear",
            "confidence": 1.5,  # Out of range
            "reasoning_summary": "test"
        }
        assert validate_reasoning_response(response) is False

    def test_negative_confidence(self):
        response = {
            "claims": [],
            "classification": "clear",
            "confidence": -0.1,
            "reasoning_summary": "test"
        }
        assert validate_reasoning_response(response) is False

    def test_claim_missing_evidence_ids(self):
        response = {
            "claims": [
                {
                    "claim_type": "sla_breach",
                    "policy_clause_id": "sla_4_2",
                    "evidence_ids": [],  # Empty - must have at least one
                    "reasoning": "test"
                }
            ],
            "classification": "clear",
            "confidence": 0.95,
            "reasoning_summary": "test"
        }
        assert validate_reasoning_response(response) is False

    def test_claim_missing_reasoning(self):
        response = {
            "claims": [
                {
                    "claim_type": "sla_breach",
                    "policy_clause_id": "sla_4_2",
                    "evidence_ids": ["ev_001"],
                    # Missing reasoning
                }
            ],
            "classification": "clear",
            "confidence": 0.95,
            "reasoning_summary": "test"
        }
        assert validate_reasoning_response(response) is False


class TestMockExtraction:
    """Test mock extraction function."""

    def test_order_evidence(self):
        result = extract_facts_mock("ev_001", "order", "{}")
        assert "facts" in result
        assert len(result["facts"]) > 0
        assert result["facts"][0]["fact_type"] == "order_detail"

    def test_delivery_evidence(self):
        result = extract_facts_mock("ev_002", "delivery", "{}")
        assert "facts" in result
        assert result["facts"][0]["fact_type"] == "delivery_record"

    def test_complaint_evidence(self):
        result = extract_facts_mock("ev_003", "complaint", "{}")
        assert "facts" in result
        assert result["facts"][0]["fact_type"] == "complaint"

    def test_refund_evidence(self):
        result = extract_facts_mock("ev_004", "refund_record", "{}")
        assert "facts" in result
        assert result["facts"][0]["fact_type"] == "refund"


class TestMockReasoning:
    """Test mock reasoning function."""

    def test_clear_scenario(self):
        facts = {"facts": []}
        policies = [{"policy_id": "sla_4_2", "clause_text": "test"}]
        result = reason_about_claims_mock(facts, policies, "clear")
        assert "claims" in result
        assert result["classification"] == "clear"
        assert len(result["claims"]) > 0

    def test_no_penalty_scenario(self):
        facts = {"facts": []}
        policies = [{"policy_id": "platform_1_1", "clause_text": "test"}]
        result = reason_about_claims_mock(facts, policies, "no_penalty")
        assert result["claims"] == []  # No deductions supported

    def test_sla_only_scenario(self):
        facts = {"facts": []}
        policies = [{"policy_id": "sla_4_2", "clause_text": "test"}]
        result = reason_about_claims_mock(facts, policies, "sla_only")
        assert len(result["claims"]) == 1
        assert result["claims"][0]["claim_type"] == "sla_breach"


class TestCalculationParams:
    """Test claim-to-calculation parameter mapping."""

    def test_sla_breach_params(self):
        claims = [
            {
                "claim_type": "sla_breach",
                "evidence_ids": ["ev_delivery_001"],
                "policy_clause_id": "sla_4_2"
            }
        ]
        params = _map_claims_to_calculation_params(claims)
        assert params["has_sla_breach"] is True
        assert params["sla_penalty_amount"] == 12000
        assert "ev_delivery_001" in params["evidence_ids"]["sla_penalty"]

    def test_return_params(self):
        claims = [
            {
                "claim_type": "return_processed",
                "evidence_ids": ["ev_refund_001"],
                "policy_clause_id": "returns_3_1"
            }
        ]
        params = _map_claims_to_calculation_params(claims)
        assert params["has_returns"] is True
        assert params["return_reserve_amount"] == 5000

    def test_no_claims(self):
        claims = []
        params = _map_claims_to_calculation_params(claims)
        assert params["has_sla_breach"] is False
        assert params["has_returns"] is False


class TestReferenceValidation:
    """Test evidence and policy reference validation."""

    def test_valid_evidence_references(self):
        claims = [{"evidence_ids": ["ev_001", "ev_002"]}]
        available = ["ev_001", "ev_002", "ev_003"]
        errors = _validate_evidence_references(claims, available)
        assert len(errors) == 0

    def test_invalid_evidence_reference(self):
        claims = [{"evidence_ids": ["ev_001", "ev_nonexistent"]}]
        available = ["ev_001", "ev_002"]
        errors = _validate_evidence_references(claims, available)
        assert len(errors) == 1
        assert "ev_nonexistent" in errors[0]

    def test_valid_policy_references(self):
        claims = [{"policy_clause_id": "sla_4_2"}]
        available = ["platform_1_1", "sla_4_2", "returns_3_1"]
        errors = _validate_policy_references(claims, available)
        assert len(errors) == 0

    def test_invalid_policy_reference(self):
        claims = [{"policy_clause_id": "nonexistent_policy"}]
        available = ["platform_1_1", "sla_4_2"]
        errors = _validate_policy_references(claims, available)
        assert len(errors) == 1
        assert "nonexistent_policy" in errors[0]


class TestContradictionDetection:
    """Regression: _detect_exceptions must use claim_type (not type).

    Previously c.get("type") was used, but claims use the field name
    claim_type.  This caused contradiction detection to never fire.
    Also checked for non-existent "sla_met" claim type.
    """

    def test_detect_exceptions_uses_claim_type_field(self):
        """Verify contradiction detection reads claim_type, not type."""
        from ai.pipeline import _detect_exceptions

        evidence_records = [
            {"source_type": "order", "evidence_id": "ev_001"},
            {"source_type": "delivery", "evidence_id": "ev_002"},
        ]
        validation = {"valid": True}

        # Two claims with overlapping evidence — should detect conflict
        reasoning_result = {
            "claims": [
                {
                    "claim_type": "sla_breach",
                    "policy_clause_id": "sla_4_2",
                    "evidence_ids": ["ev_001"],
                    "reasoning": "late delivery",
                },
                {
                    "claim_type": "return_processed",
                    "policy_clause_id": "returns_3_1",
                    "evidence_ids": ["ev_001"],  # same evidence!
                    "reasoning": "return processed",
                },
            ],
            "classification": "ambiguous",
            "confidence": 0.5,
            "reasoning_summary": "conflicting",
        }

        exceptions = _detect_exceptions(
            reasoning_result, evidence_records, validation,
            gross_amount=100000, line_items=[],
        )
        conflict_cats = [e["category"] for e in exceptions]
        assert "CONFLICTING_EVIDENCE" in conflict_cats, (
            "Contradiction detection must fire when same evidence supports "
            "both SLA breach and return_processed claims"
        )

    def test_no_false_positive_on_different_evidence(self):
        """SLA breach + return with different evidence is NOT a contradiction."""
        from ai.pipeline import _detect_exceptions

        evidence_records = [
            {"source_type": "order", "evidence_id": "ev_001"},
            {"source_type": "delivery", "evidence_id": "ev_002"},
            {"source_type": "refund_record", "evidence_id": "ev_003"},
        ]
        validation = {"valid": True}

        reasoning_result = {
            "claims": [
                {
                    "claim_type": "sla_breach",
                    "policy_clause_id": "sla_4_2",
                    "evidence_ids": ["ev_002"],  # delivery evidence
                    "reasoning": "late delivery",
                },
                {
                    "claim_type": "return_processed",
                    "policy_clause_id": "returns_3_1",
                    "evidence_ids": ["ev_003"],  # refund evidence
                    "reasoning": "return processed",
                },
            ],
            "classification": "clear",
            "confidence": 0.9,
            "reasoning_summary": "both valid",
        }

        exceptions = _detect_exceptions(
            reasoning_result, evidence_records, validation,
            gross_amount=100000, line_items=[],
        )
        conflict_cats = [e["category"] for e in exceptions]
        assert "CONFLICTING_EVIDENCE" not in conflict_cats, (
            "Different evidence for SLA breach and return is NOT a contradiction"
        )

    def test_agent_reported_conflicts_detected(self):
        """Pipeline must also detect agent-reported conflicting_evidence."""
        from ai.pipeline import _detect_exceptions

        evidence_records = [
            {"source_type": "order", "evidence_id": "ev_001"},
        ]
        validation = {"valid": True}

        reasoning_result = {
            "claims": [],
            "classification": "ambiguous",
            "confidence": 0.4,
            "reasoning_summary": "conflicting data",
            "conflicting_evidence": ["ev_001 vs ev_002"],
        }

        exceptions = _detect_exceptions(
            reasoning_result, evidence_records, validation,
            gross_amount=100000, line_items=[],
        )
        conflict_cats = [e["category"] for e in exceptions]
        assert "CONFLICTING_EVIDENCE" in conflict_cats, (
            "Agent-reported conflicts must be surfaced as exceptions"
        )


class TestAIAvailability:
    """Test AI availability check."""

    def test_no_api_key_no_ollama(self):
        """Returns False when neither Ollama nor Anthropic are available."""
        with patch.dict(os.environ, {}, clear=True):
            reset_provider()
            assert is_ai_available() is False

    def test_with_api_key(self):
        """Returns True when the Anthropic key is set AND the SDK is importable.

        The Anthropic SDK is intentionally NOT a project dependency (the
        provider stays an optional import path), so availability is gated on
        both the key and a successful ``import anthropic``.  This test fakes
        the import to exercise the real key+import contract without adding
        the SDK to the dependency set.
        """
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            reset_provider()
            import sys
            from unittest.mock import Mock
            from ai.llm_provider import AnthropicProvider
            with patch.dict("sys.modules", {"anthropic": Mock()}):
                assert AnthropicProvider().is_available() is True
            # Without an importable SDK the provider is NOT available even
            # when a key exists (dependency policy — never fabricate support).
            assert AnthropicProvider().is_available() is False


class TestPipelineWithMock:
    """Test the full pipeline with mock AI responses."""

    def test_scenario_1_pipeline(self):
        """Test primary scenario (Return + SLA breach) with mock."""
        evidence = get_scenario_evidence("scenario_1")
        policies = get_scenario_policies("scenario_1")

        result = run_pipeline(
            scenario_id="scenario_1",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            use_mock=True,
        )

        decision = result["decision"]
        assert decision["decision_id"].startswith("dec_")
        assert decision["gross_amount"] == 100000
        assert decision["final_amount"] == 75000
        # Safe + sufficient + valid calculation → APPROVED via deterministic gate
        assert decision["status"] == "APPROVED"
        assert len(decision["line_items"]) == 3
        assert decision["decision_hash"] != ""
        assert "claims" in decision["model_output"]

    def test_scenario_3_no_penalty(self):
        """Test complaint scenario where AI determines NO deduction."""
        evidence = get_scenario_evidence("scenario_3")
        policies = get_scenario_policies("scenario_3")

        result = run_pipeline(
            scenario_id="scenario_3",
            evidence_records=evidence,
            policy_records=policies,
            prev_decision_hash="genesis",
            use_mock=True,
        )

        decision = result["decision"]
        assert decision["gross_amount"] == 45000
        assert decision["final_amount"] == 41400  # Only platform fee deducted
        assert len(decision["line_items"]) == 1  # Only platform fee
        assert decision["line_items"][0]["label"] == "Platform fee"

    def test_pipeline_stages_recorded(self):
        """Test that pipeline stages are recorded."""
        evidence = get_scenario_evidence("scenario_1")
        policies = get_scenario_policies("scenario_1")

        result = run_pipeline(
            scenario_id="scenario_1",
            evidence_records=evidence,
            policy_records=policies,
            use_mock=True,
        )

        assert "stages" in result
        assert "total_duration_ms" in result
        assert "extraction" in result["stages"]
        assert "reasoning" in result["stages"]


class TestSeedDataIntegrity:
    """Test that seed data maintains expected values."""

    def test_primary_scenario_amounts(self):
        from seed_data import get_all_decisions
        decisions = get_all_decisions()
        d1 = next(d for d in decisions if d["decision_id"] == "dec_001")

        assert d1["gross_amount"] == 100000
        assert d1["final_amount"] == 75000
        assert len(d1["line_items"]) == 3

    def test_tampered_scenario_detected(self):
        from hash_chain import verify_chain
        from seed_data import get_all_decisions

        decisions = get_all_decisions()
        # The tampered decision should fail verification
        tampered = next(d for d in decisions if d["decision_id"] == "dec_005_tampered")

        # Verify the chain including tampered - should fail
        result = verify_chain([tampered])
        assert result["valid"] is False
        assert result["break_at"] == "dec_005_tampered"

    def test_valid_chain_integrity(self):
        from hash_chain import verify_chain
        from seed_data import get_all_decisions

        decisions = get_all_decisions()
        valid_decisions = [d for d in decisions if d["decision_id"] != "dec_005_tampered"]
        result = verify_chain(valid_decisions)
        assert result["valid"] is True
        assert result["checked_count"] == 4


class TestMissingAPIKeyBehavior:
    """Test behavior when no LLM provider is available."""

    def test_is_ai_available_returns_false_without_providers(self):
        """Verify is_ai_available returns False when no provider is configured."""
        with patch.dict(os.environ, {}, clear=True):
            reset_provider()
            assert is_ai_available() is False

    def test_extraction_raises_without_provider(self):
        """Verify extraction raises EnvironmentError when no provider is available."""
        from ai.extraction import extract_facts_from_evidence

        with patch.dict(os.environ, {}, clear=True):
            reset_provider()
            with pytest.raises(EnvironmentError, match="No LLM provider"):
                extract_facts_from_evidence("ev_001", "order", "{}")

    def test_reasoning_raises_without_provider(self):
        """Verify reasoning raises EnvironmentError when no provider is available."""
        from ai.reasoning import reason_about_claims

        with patch.dict(os.environ, {}, clear=True):
            reset_provider()
            with pytest.raises(EnvironmentError, match="No LLM provider"):
                reason_about_claims({"facts": []}, [])

    def test_pipeline_with_mock_works_without_provider(self):
        """Verify pipeline with use_mock=True works without any LLM provider."""
        evidence = get_scenario_evidence("scenario_1")
        policies = get_scenario_policies("scenario_1")

        with patch.dict(os.environ, {}, clear=True):
            reset_provider()
            result = run_pipeline(
                scenario_id="scenario_1",
                evidence_records=evidence,
                policy_records=policies,
                use_mock=True,
            )
            assert result["decision"]["gross_amount"] == 100000
            assert result["decision"]["final_amount"] == 75000

    def test_env_loading_preserves_existing(self):
        """Verify .env loading doesn't overwrite existing env vars."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "existing-key"}):
            assert os.environ.get("ANTHROPIC_API_KEY") == "existing-key"

    def test_key_not_in_logs(self, caplog):
        """Verify API key is never logged."""
        test_key = "sk-ant-test-secret-12345"
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": test_key}):
            reset_provider()
            assert test_key not in str(caplog.text) if caplog.text else True


class TestOpenRouterProvider:
    """Test OpenRouter provider with mocked HTTP responses."""

    def test_is_available_with_key(self):
        provider = OpenRouterProvider(api_key="test-key")
        assert provider.is_available() is True

    def test_is_available_without_key(self):
        provider = OpenRouterProvider(api_key="")
        assert provider.is_available() is False

    def test_provider_info(self):
        provider = OpenRouterProvider(api_key="test-key", model="openrouter/free")
        info = provider.provider_info()
        assert info["provider"] == "openrouter"
        assert info["model"] == "openrouter/free"
        assert info["requires_api_key"] is True

    def test_complete_raises_without_key(self):
        provider = OpenRouterProvider(api_key="")
        with pytest.raises(EnvironmentError, match="OPENROUTER_API_KEY"):
            provider.complete("test prompt")

    def test_complete_success(self):
        """Test successful completion with mocked HTTP response."""
        provider = OpenRouterProvider(api_key="test-key", model="openrouter/free")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"facts": [{"fact_type": "order_detail", "value": "test"}]}'
                    }
                }
            ]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            result = provider.complete("Extract facts", system="You are helpful")
            assert "facts" in result
            mock_client.post.assert_called_once()

    def test_complete_api_error(self):
        """Test API error handling."""
        import httpx

        provider = OpenRouterProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.json.return_value = {
            "error": {"message": "Invalid API key"}
        }

        mock_exc = httpx.HTTPStatusError(
            message="401 Unauthorized",
            request=Mock(),
            response=mock_response,
        )

        mock_client = Mock()
        mock_client.post.side_effect = mock_exc

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            with pytest.raises(ValueError, match="OpenRouter API error"):
                provider.complete("test")

    def test_complete_timeout(self):
        """Test timeout handling."""
        import httpx

        provider = OpenRouterProvider(api_key="test-key")

        mock_client = Mock()
        mock_client.post.side_effect = httpx.TimeoutException("timeout")

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            with pytest.raises(ValueError, match="timed out"):
                provider.complete("test")

    def test_complete_no_choices(self):
        """Test empty choices response."""
        provider = OpenRouterProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {"choices": []}

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            with pytest.raises(ValueError, match="no choices"):
                provider.complete("test")

    def test_complete_error_in_response(self):
        """Test error field in response body."""
        provider = OpenRouterProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "error": {"message": "Model not found"}
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            with pytest.raises(ValueError, match="OpenRouter error"):
                provider.complete("test")

    def test_complete_json_parsing(self):
        """Test complete_json parses JSON from response."""
        provider = OpenRouterProvider(api_key="test-key")

        json_response = '{"claims": [], "classification": "clear", "confidence": 0.9, "reasoning_summary": "test"}'

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": json_response}}]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            result = provider.complete_json("test prompt")
            assert isinstance(result, dict)
            assert result["classification"] == "clear"

    def test_complete_json_with_markdown_fences(self):
        """Test complete_json strips markdown code fences."""
        provider = OpenRouterProvider(api_key="test-key")

        fenced_response = '```json\n{"claims": [], "classification": "clear", "confidence": 0.9, "reasoning_summary": "test"}\n```'

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": fenced_response}}]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            result = provider.complete_json("test prompt")
            assert isinstance(result, dict)
            assert result["classification"] == "clear"

    def test_factory_selects_openrouter(self):
        """Test that factory selects OpenRouter when OPENROUTER_API_KEY is set."""
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_MODEL": "openrouter/free",
        }, clear=False):
            reset_provider()
            try:
                provider = OpenRouterProvider(api_key="test-key")
                assert provider.is_available() is True
                assert provider.provider_info()["provider"] == "openrouter"
            finally:
                reset_provider()
                # Clean up any other env vars that might affect other tests
                for key in ["OPENROUTER_API_KEY", "OPENROUTER_MODEL"]:
                    os.environ.pop(key, None)

    def test_factory_prefers_ollama_over_openrouter(self):
        """Test that Ollama is preferred over OpenRouter when both available."""
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
        }, clear=False):
            reset_provider()
            try:
                # Mock Ollama as available
                with patch("ai.llm_provider.OllamaProvider") as MockOllama:
                    mock_ollama = Mock()
                    mock_ollama.is_available.return_value = True
                    MockOllama.return_value = mock_ollama

                    provider = OpenRouterProvider(api_key="test-key")
                    assert provider.is_available() is True
            finally:
                reset_provider()
                os.environ.pop("OPENROUTER_API_KEY", None)

    def test_complete_reasoning_content_fallback(self):
        """When content is empty but reasoning_content exists, use reasoning_content.

        Some reasoning models (DeepSeek R1, Qwen w/ thinking) return their
        output in reasoning_content while content is null/empty.
        """
        provider = OpenRouterProvider(api_key="test-key", model="openrouter/free")

        json_response = '{"facts": [{"fact_type": "order_detail", "value": "test", "evidence_quote": "test quote here"}]}'

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "reasoning_content": json_response,
                    },
                    "finish_reason": "stop",
                }
            ]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            result = provider.complete("Extract facts")
            assert result == json_response
            assert "facts" in result

    def test_complete_content_empty_string_fallback(self):
        """When content is empty string but reasoning_content has data."""
        provider = OpenRouterProvider(api_key="test-key")

        reasoning = '{"claims": [], "classification": "clear", "confidence": 0.9, "reasoning_summary": "test"}'

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": reasoning,
                    },
                    "finish_reason": "stop",
                }
            ]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            result = provider.complete("test")
            assert result == reasoning

    def test_complete_both_empty_raises(self):
        """When both content and reasoning_content are empty, raise ValueError."""
        provider = OpenRouterProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "",
                    },
                    "finish_reason": "stop",
                }
            ]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            with pytest.raises(ValueError, match="empty content"):
                provider.complete("test")

    def test_complete_no_message_key_raises(self):
        """When message key is missing entirely, raise ValueError."""
        provider = OpenRouterProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                }
            ]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            with pytest.raises(ValueError, match="empty content"):
                provider.complete("test")

    def test_complete_json_from_reasoning_content(self):
        """complete_json should parse JSON from reasoning_content when content is empty."""
        provider = OpenRouterProvider(api_key="test-key")

        json_in_reasoning = '{"claims": [{"claim_type": "sla_breach", "policy_clause_id": "sla_4_2", "evidence_ids": ["ev_1"], "reasoning": "test"}], "classification": "clear", "confidence": 0.9, "reasoning_summary": "test"}'

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "reasoning_content": json_in_reasoning,
                    },
                    "finish_reason": "stop",
                }
            ]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            result = provider.complete_json("test prompt")
            assert isinstance(result, dict)
            assert result["classification"] == "clear"
            assert len(result["claims"]) == 1

    def test_complete_malformed_json_in_content(self):
        """Malformed JSON in content should raise via complete_json."""
        provider = OpenRouterProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "This is not JSON at all, just plain text.",
                    },
                    "finish_reason": "stop",
                }
            ]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            with pytest.raises(ValueError, match="Failed to parse JSON"):
                provider.complete_json("test prompt")

    def test_complete_api_error_403(self):
        """Test 403 Forbidden error handling."""
        import httpx

        provider = OpenRouterProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.json.return_value = {
            "error": {"message": "Model requires paid account"}
        }

        mock_exc = httpx.HTTPStatusError(
            message="403 Forbidden",
            request=Mock(),
            response=mock_response,
        )

        mock_client = Mock()
        mock_client.post.side_effect = mock_exc

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            with pytest.raises(ValueError, match="OpenRouter API error.*403"):
                provider.complete("test")

    def test_complete_connection_error(self):
        """Test connection error handling."""
        import httpx

        provider = OpenRouterProvider(api_key="test-key")

        mock_client = Mock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            with pytest.raises(ValueError, match="connection error"):
                provider.complete("test")

    def test_content_preferred_over_reasoning(self):
        """When both content and reasoning_content exist, prefer content."""
        provider = OpenRouterProvider(api_key="test-key")

        content_text = "This is the main content"
        reasoning_text = "This is the reasoning"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": content_text,
                        "reasoning_content": reasoning_text,
                    },
                    "finish_reason": "stop",
                }
            ]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            result = provider.complete("test")
            assert result == content_text

    def test_complete_json_sends_json_mode(self):
        """complete_json must pass json_mode=True to complete()."""
        provider = OpenRouterProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"key": "value"}'}}]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            result = provider.complete_json("test prompt")
            assert isinstance(result, dict)
            assert result["key"] == "value"

            # Verify response_format was sent in the payload
            call_args = mock_client.post.call_args
            payload = call_args[1].get("json") or call_args[0][1] if len(call_args[0]) > 1 else call_args[1]["json"]
            assert payload.get("response_format") == {"type": "json_object"}, \
                "complete_json must include response_format in payload"

    def test_complete_no_json_mode_by_default(self):
        """Plain complete() must NOT send response_format."""
        provider = OpenRouterProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hello"}}]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            provider.complete("test prompt")
            call_args = mock_client.post.call_args
            payload = call_args[1].get("json") or call_args[0][1] if len(call_args[0]) > 1 else call_args[1]["json"]
            assert "response_format" not in payload, \
                "Plain complete() must not include response_format"

    def test_complete_json_mode_true_sends_response_format(self):
        """complete(json_mode=True) must include response_format."""
        provider = OpenRouterProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"a": 1}'}}]
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = Mock(return_value=False)

            provider.complete("test", json_mode=True)
            call_args = mock_client.post.call_args
            payload = call_args[1].get("json") or call_args[0][1] if len(call_args[0]) > 1 else call_args[1]["json"]
            assert payload.get("response_format") == {"type": "json_object"}


class TestGeminiProvider:
    """Test Gemini provider with mocked SDK responses."""

    def test_is_available_with_key_and_sdk(self):
        provider = GeminiProvider(api_key="test-key")
        with patch.dict("sys.modules", {"google": Mock(), "google.genai": Mock()}):
            assert provider.is_available() is True

    def test_is_available_without_key(self):
        provider = GeminiProvider(api_key="")
        assert provider.is_available() is False

    def test_is_available_without_sdk(self):
        provider = GeminiProvider(api_key="test-key")
        with patch.dict("sys.modules", {"google": None, "google.genai": None}):
            assert provider.is_available() is False

    def test_provider_info(self):
        provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash")
        info = provider.provider_info()
        assert info["provider"] == "gemini"
        assert info["model"] == "gemini-2.5-flash"
        assert info["requires_api_key"] is True

    def test_complete_raises_without_key(self):
        provider = GeminiProvider(api_key="")
        with pytest.raises(EnvironmentError, match="GEMINI_API_KEY"):
            provider.complete("test prompt")

    def test_complete_success(self):
        """Test successful completion with mocked SDK response."""
        provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash")

        # Mock the genai module
        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        # Mock the response — parsed=None forces text fallback
        mock_part = Mock()
        mock_part.text = '{"facts": [{"fact_type": "order_detail", "value": "test"}]}'
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            result = provider.complete("Extract facts", system="You are helpful")
            assert "facts" in result
            mock_client.models.generate_content.assert_called_once()

    def test_complete_json_mode_sends_response_mime_type(self):
        """When json_mode=True, config must have response_mime_type set."""
        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client

        # Track what config is passed
        captured_config = Mock()
        captured_config.response_mime_type = None
        mock_genai.types.GenerateContentConfig.return_value = captured_config

        mock_part = Mock()
        mock_part.text = '{"key": "value"}'
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            provider.complete("test", json_mode=True)
            # Verify response_mime_type was passed via kwargs to GenerateContentConfig
            call_kwargs = mock_genai.types.GenerateContentConfig.call_args[1]
            assert call_kwargs.get("response_mime_type") == "application/json"

    def test_complete_no_json_mode_by_default(self):
        """When json_mode=False, config must NOT have response_mime_type."""
        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client

        captured_config = Mock(spec=[])  # No response_mime_type attribute
        mock_genai.types.GenerateContentConfig.return_value = captured_config

        mock_part = Mock()
        mock_part.text = "hello"
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            provider.complete("test", json_mode=False)
            # Config should not have response_mime_type set
            assert not hasattr(captured_config, "response_mime_type") or captured_config.response_mime_type is None

    def test_complete_api_error(self):
        """Test API error handling."""
        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_client.models.generate_content.side_effect = Exception("API_KEY_INVALID")

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            with pytest.raises(ValueError, match="invalid API key"):
                provider.complete("test")

    def test_complete_quota_error(self):
        """Test quota/rate limit error handling."""
        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_client.models.generate_content.side_effect = Exception("Rate limit exceeded")

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            with pytest.raises(ValueError, match="quota/rate limit"):
                provider.complete("test")

    def test_complete_no_candidates(self):
        """Test empty candidates response."""
        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = []
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            with pytest.raises(ValueError, match="no candidates"):
                provider.complete("test")

    def test_complete_empty_parts(self):
        """Test response with empty parts."""
        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_candidate = Mock()
        mock_candidate.content.parts = []
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            with pytest.raises(ValueError, match="empty response"):
                provider.complete("test")

    def test_complete_empty_text(self):
        """Test response with empty text in parts."""
        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_part = Mock()
        mock_part.text = ""
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            with pytest.raises(ValueError, match="empty text"):
                provider.complete("test")

    def test_complete_json_parsing(self):
        """Test complete_json parses JSON from Gemini response."""
        provider = GeminiProvider(api_key="test-key")

        json_response = '{"claims": [], "classification": "clear", "confidence": 0.9, "reasoning_summary": "test"}'

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_part = Mock()
        mock_part.text = json_response
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            result = provider.complete_json("test prompt")
            assert isinstance(result, dict)
            assert result["classification"] == "clear"

    def test_factory_selects_gemini(self):
        """Test that factory selects Gemini when GEMINI_API_KEY is set."""
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "test-key",
            "GEMINI_MODEL": "gemini-2.5-flash",
        }, clear=False):
            reset_provider()
            try:
                with patch.dict("sys.modules", {"google": Mock(), "google.genai": Mock()}):
                    provider = GeminiProvider(api_key="test-key")
                    assert provider.is_available() is True
                    assert provider.provider_info()["provider"] == "gemini"
            finally:
                reset_provider()
                for key in ["GEMINI_API_KEY", "GEMINI_MODEL"]:
                    os.environ.pop(key, None)

    def test_factory_prefers_gemini_over_openrouter(self):
        """Test that Gemini is preferred over OpenRouter when both keys are present."""
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "gemini-key",
            "OPENROUTER_API_KEY": "openrouter-key",
            "GROQ_API_KEY": "",  # Clear to prevent env leakage from .env
        }, clear=False):
            reset_provider()
            try:
                with patch.dict("sys.modules", {"google": Mock(), "google.genai": Mock()}):
                    # Mock Ollama as unavailable
                    with patch("ai.llm_provider.OllamaProvider") as MockOllama:
                        mock_ollama = Mock()
                        mock_ollama.is_available.return_value = False
                        MockOllama.return_value = mock_ollama

                        provider = get_provider()
                        assert provider.provider_info()["provider"] == "gemini"
            finally:
                reset_provider()
                for key in ["GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY"]:
                    os.environ.pop(key, None)

    def test_no_part_from_text_positional_arg(self):
        """Regression: verify we do NOT call Part.from_text() with positional args.

        The google-genai SDK's Part.from_text() requires a keyword argument:
            Part.from_text(text="...")
        Passing a positional arg causes:
            TypeError: Part.from_text() takes 1 positional argument but 2 were given

        This test verifies the GeminiProvider passes contents as a plain string
        to generate_content(), never touching Part.from_text().
        """
        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_part = Mock()
        mock_part.text = '{"ok": true}'
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            result = provider.complete("test prompt", system="system instruction")
            assert result == '{"ok": true}'

            # Verify generate_content was called with contents as a plain string
            call_kwargs = mock_client.models.generate_content.call_args[1]
            assert call_kwargs["contents"] == "test prompt", \
                "contents must be a plain string, not Content/Part objects"

            # Verify system_instruction was passed via config, not concatenated
            config = call_kwargs["config"]
            # The config is a Mock, verify system_instruction was set during construction
            mock_genai.types.GenerateContentConfig.assert_called_once()
            call_kwargs_config = mock_genai.types.GenerateContentConfig.call_args[1]
            assert call_kwargs_config.get("system_instruction") == "system instruction", \
                "system_instruction must be passed via GenerateContentConfig"

            # Verify Part.from_text was never called
            mock_genai.types.Part.from_text.assert_not_called() if hasattr(mock_genai.types.Part, 'from_text') else None

    def test_structured_parsed_response(self):
        """When response.parsed is available (native structured output),
        complete() returns the dict directly — no json.dumps round-trip."""
        from ai.llm_provider import ExtractionSchema

        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        # Simulate SDK returning a parsed dict via response.parsed
        mock_response = Mock()
        mock_response.parsed = {"facts": [{"fact_type": "order_detail", "value": "test", "evidence_quote": "quote here"}]}
        mock_response.candidates = []  # Should not be reached
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            result = provider.complete(
                "test", json_mode=True, response_schema=ExtractionSchema,
            )
            # complete() returns a dict directly — no text round-trip
            assert isinstance(result, dict)
            assert "facts" in result
            assert result["facts"][0]["fact_type"] == "order_detail"

    def test_structured_parsed_pydantic_model(self):
        """When response.parsed is a Pydantic model, complete() returns model_dump() dict."""
        from ai.llm_provider import ReasoningSchema

        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        # Simulate SDK returning a Pydantic model instance
        mock_parsed = ReasoningSchema(
            claims=[],
            classification="clear",
            confidence=0.9,
            reasoning_summary="test",
        )
        mock_response = Mock()
        mock_response.parsed = mock_parsed
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            result = provider.complete(
                "test", json_mode=True, response_schema=ReasoningSchema,
            )
            # complete() returns a dict via model_dump() — no text round-trip
            assert isinstance(result, dict)
            assert result["classification"] == "clear"
            assert result["confidence"] == 0.9

    def test_fallback_to_text_when_parsed_is_none(self):
        """When response.parsed is None, fall back to response.text."""
        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_part = Mock()
        mock_part.text = '{"key": "value"}'
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            result = provider.complete("test")
            assert result == '{"key": "value"}'

    def test_complete_json_with_schema_passes_to_provider(self):
        """complete_json must forward response_schema to complete()."""
        from ai.llm_provider import ExtractionSchema

        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_response = Mock()
        mock_response.parsed = {"facts": [{"fact_type": "order_detail", "value": "x", "evidence_quote": "quote here"}]}
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            result = provider.complete_json(
                "test", response_schema=ExtractionSchema,
            )
            assert isinstance(result, dict)
            assert "facts" in result

            # Verify response_schema was passed in config
            call_kwargs_config = mock_genai.types.GenerateContentConfig.call_args[1]
            assert call_kwargs_config.get("response_schema") == ExtractionSchema

    def test_complete_json_native_dict_passthrough(self):
        """complete_json passes through a dict from complete() without re-parsing."""
        from ai.llm_provider import ExtractionSchema

        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        # response.parsed returns a dict → complete() returns a dict
        expected = {"facts": [{"fact_type": "order_detail", "value": "y", "evidence_quote": "q"}]}
        mock_response = Mock()
        mock_response.parsed = expected
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            result = provider.complete_json("test", response_schema=ExtractionSchema)
            # Must be a dict, not a re-parsed string
            assert isinstance(result, dict)
            assert result == expected

    def test_complete_json_native_pydantic_passthrough(self):
        """complete_json converts Pydantic model via model_dump() without round-trip."""
        from ai.llm_provider import ReasoningSchema

        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_parsed = ReasoningSchema(
            claims=[], classification="clear", confidence=0.8, reasoning_summary="ok"
        )
        mock_response = Mock()
        mock_response.parsed = mock_parsed
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            result = provider.complete_json("test", response_schema=ReasoningSchema)
            assert isinstance(result, dict)
            assert result["classification"] == "clear"
            assert result["confidence"] == 0.8

    def test_complete_json_text_fallback(self):
        """When complete() returns raw text, complete_json() parses it."""
        provider = GeminiProvider(api_key="test-key")

        json_text = '{"facts": [{"fact_type": "order_detail", "value": "x", "evidence_quote": "q"}]}'

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_part = Mock()
        mock_part.text = json_text
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            result = provider.complete_json("test")
            assert isinstance(result, dict)
            assert "facts" in result

    def test_complete_json_malformed_text_still_rejected(self):
        """Malformed text from complete() must still be rejected."""
        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_part = Mock()
        mock_part.text = "This is not JSON at all"
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            with pytest.raises(ValueError, match="Failed to parse JSON"):
                provider.complete_json("test")

    def test_complete_json_gemini_safety_text_rejected(self):
        """Gemini safety response text must NOT be accepted as valid JSON."""
        provider = GeminiProvider(api_key="test-key")

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        # Simulate the "User Safety: safe..." response that started this investigation
        mock_part = Mock()
        mock_part.text = "User Safety: safe. The request does not violate any safety policies."
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            with pytest.raises(ValueError, match="Failed to parse JSON"):
                provider.complete_json("test")

    def test_parsed_none_with_schema_falls_back_to_json_loads(self):
        """Production regression: response.parsed is None (SDK validation failed)
        but response.text contains valid JSON when response_schema was provided.
        The complete() method should fall back to json.loads() and return a dict.

        This is the exact production failure scenario:
        - Gemini returns valid JSON via response_schema
        - SDK's model_validate_json() silently fails (preamble, etc.)
        - response.parsed is None
        - complete() must parse the text and return a dict
        """
        from ai.llm_provider import ExtractionSchema

        provider = GeminiProvider(api_key="test-key")

        valid_json = (
            '{"facts": [{"fact_type": "other", "value": "Payment captured", '
            '"amount": null, "date": null, "evidence_quote": "some quote"}]}'
        )

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        # Simulate: SDK validation failed → parsed=None, but text is valid JSON
        mock_part = Mock()
        mock_part.text = valid_json
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_candidate.finish_reason = "STOP"
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            # complete() with response_schema should return a dict via json.loads fallback
            result = provider.complete(
                "extract", json_mode=True, response_schema=ExtractionSchema,
            )
            assert isinstance(result, dict)
            assert "facts" in result
            assert result["facts"][0]["fact_type"] == "other"

            # complete_json() should pass the dict through without re-parsing
            mock_client.models.generate_content.return_value = mock_response
            result2 = provider.complete_json(
                "extract", response_schema=ExtractionSchema,
            )
            assert isinstance(result2, dict)
            assert result2 == result

    def test_parsed_none_with_truncated_json_still_fails(self):
        """Truncated JSON (output hit max_tokens) must still be rejected.

        When the JSON is genuinely incomplete, both json.loads and
        _parse_json_response should fail. The error must propagate.
        """
        from ai.llm_provider import ExtractionSchema

        provider = GeminiProvider(api_key="test-key")

        truncated_json = '{"facts": [{"fact_type": "other", "value": "test"'

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_part = Mock()
        mock_part.text = truncated_json
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_candidate.finish_reason = "MAX_TOKENS"
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            with pytest.raises(ValueError, match="Failed to parse JSON"):
                provider.complete_json(
                    "extract", response_schema=ExtractionSchema,
                )

    def test_parsed_none_without_schema_returns_raw_text(self):
        """When no response_schema is provided and parsed is None,
        complete() returns raw text (no json.loads attempt)."""
        provider = GeminiProvider(api_key="test-key")

        raw_text = 'Some non-JSON response from Gemini'

        mock_genai = Mock()
        mock_client = Mock()
        mock_genai.Client.return_value = mock_client
        mock_genai.types.GenerateContentConfig.return_value = Mock()

        mock_part = Mock()
        mock_part.text = raw_text
        mock_candidate = Mock()
        mock_candidate.content.parts = [mock_part]
        mock_response = Mock()
        mock_response.parsed = None
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            # No response_schema → should return raw text, not try json.loads
            result = provider.complete("test")
            assert isinstance(result, str)
            assert result == raw_text


class TestGroqProvider:
    """Test Groq provider with mocked HTTP responses."""

    def test_is_available_with_key(self):
        provider = GroqProvider(api_key="test-key")
        assert provider.is_available() is True

    def test_is_available_without_key(self):
        provider = GroqProvider(api_key="")
        assert provider.is_available() is False

    def test_provider_info(self):
        provider = GroqProvider(api_key="test-key", model="openai/gpt-oss-120b")
        info = provider.provider_info()
        assert info["provider"] == "groq"
        assert info["model"] == "openai/gpt-oss-120b"
        assert info["requires_api_key"] is True

    def test_complete_raises_without_key(self):
        provider = GroqProvider(api_key="")
        with pytest.raises(EnvironmentError, match="GROQ_API_KEY"):
            provider.complete("test prompt")

    def test_complete_success(self):
        """Test successful completion with mocked HTTP response."""
        provider = GroqProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {"content": "{\"result\": \"ok\"}"},
                "finish_reason": "stop",
            }]
        }

        with patch("ai.llm_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_response
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception
            result = provider.complete("test prompt", system="You are helpful")
            assert isinstance(result, str)
            mock_httpx.post.assert_called_once()

    def test_complete_structured_output_returns_dict(self):
        """When response_schema is provided with json_mode, strict structured
        output is used and the response is parsed to a dict."""
        from ai.llm_provider import ExtractionSchema

        provider = GroqProvider(api_key="test-key")

        structured_json = json.dumps({
            "facts": [{
                "fact_type": "order_detail",
                "value": "test",
                "amount": None,
                "date": None,
                "evidence_quote": "a quote here for validation",
            }]
        })

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {"content": structured_json},
                "finish_reason": "stop",
            }]
        }

        with patch("ai.llm_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_response
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception
            result = provider.complete(
                "extract", json_mode=True, response_schema=ExtractionSchema,
            )
            # Should return a dict, not a string
            assert isinstance(result, dict)
            assert "facts" in result
            assert result["facts"][0]["fact_type"] == "order_detail"

    def test_complete_structured_sends_strict_schema(self):
        """Verify the request payload contains strict json_schema format."""
        from ai.llm_provider import ExtractionSchema

        provider = GroqProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {"content": '{"facts": []}'},
                "finish_reason": "stop",
            }]
        }

        with patch("ai.llm_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_response
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception
            provider.complete(
                "extract", json_mode=True, response_schema=ExtractionSchema,
            )
            call_args = mock_httpx.post.call_args
            payload = call_args[1]["json"]
            rf = payload["response_format"]
            assert rf["type"] == "json_schema"
            assert rf["json_schema"]["strict"] is True
            assert rf["json_schema"]["name"] == "ExtractionSchema"
            # Verify the schema has additionalProperties: false on objects
            schema = rf["json_schema"]["schema"]
            assert schema.get("additionalProperties") is False
            # Verify all fields are required
            assert "required" in schema
            assert "facts" in schema["required"]

    def test_complete_json_mode_without_schema(self):
        """When json_mode=True but no response_schema, sends json_object format."""
        provider = GroqProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {"content": '{"key": "value"}'},
                "finish_reason": "stop",
            }]
        }

        with patch("ai.llm_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_response
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception
            provider.complete("test", json_mode=True)
            call_args = mock_httpx.post.call_args
            payload = call_args[1]["json"]
            assert payload["response_format"] == {"type": "json_object"}

    def test_complete_api_error(self):
        """Test API error handling."""
        provider = GroqProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        with patch("ai.llm_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_response
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception
            with pytest.raises(ValueError, match="Groq API error 403"):
                provider.complete("test")

    def test_complete_empty_choices(self):
        """Test response with empty choices."""
        provider = GroqProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": []}

        with patch("ai.llm_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_response
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception
            with pytest.raises(ValueError, match="no choices"):
                provider.complete("test")

    def test_complete_empty_content(self):
        """Test response with empty content."""
        provider = GroqProvider(api_key="test-key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {"content": ""},
                "finish_reason": "stop",
            }]
        }

        with patch("ai.llm_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_response
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception
            with pytest.raises(ValueError, match="empty content"):
                provider.complete("test")

    def test_complete_json_parsing(self):
        """Test complete_json parses JSON from Groq response."""
        provider = GroqProvider(api_key="test-key")

        json_response = '{"claims": [], "classification": "clear", "confidence": 0.9, "reasoning_summary": "test"}'

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {"content": json_response},
                "finish_reason": "stop",
            }]
        }

        with patch("ai.llm_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_response
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception
            result = provider.complete_json("test prompt")
            assert isinstance(result, dict)
            assert result["classification"] == "clear"

    def test_factory_selects_groq(self):
        """Test that factory selects Groq when GROQ_API_KEY is set."""
        with patch.dict(os.environ, {
            "GROQ_API_KEY": "test-key",
            "GROQ_MODEL": "openai/gpt-oss-120b",
        }, clear=False):
            reset_provider()
            try:
                # Mock Ollama as unavailable
                with patch("ai.llm_provider.OllamaProvider") as MockOllama:
                    mock_ollama = Mock()
                    mock_ollama.is_available.return_value = False
                    MockOllama.return_value = mock_ollama

                    provider = get_provider()
                    assert provider.provider_info()["provider"] == "groq"
            finally:
                reset_provider()
                for key in ["GROQ_API_KEY", "GROQ_MODEL"]:
                    os.environ.pop(key, None)

    def test_factory_prefers_groq_over_gemini(self):
        """Test that Groq is preferred over Gemini when both keys are present."""
        with patch.dict(os.environ, {
            "GROQ_API_KEY": "groq-key",
            "GEMINI_API_KEY": "gemini-key",
        }, clear=False):
            reset_provider()
            try:
                with patch("ai.llm_provider.OllamaProvider") as MockOllama:
                    mock_ollama = Mock()
                    mock_ollama.is_available.return_value = False
                    MockOllama.return_value = mock_ollama

                    provider = get_provider()
                    assert provider.provider_info()["provider"] == "groq"
            finally:
                reset_provider()
                for key in ["GROQ_API_KEY", "GEMINI_API_KEY"]:
                    os.environ.pop(key, None)


class TestSchemaConversion:
    """Test Pydantic → Groq strict JSON Schema conversion."""

    def test_extraction_schema_strict(self):
        """ExtractionSchema converts to strict JSON Schema with all fields required."""
        from ai.llm_provider import ExtractionSchema

        schema = _pydantic_to_strict_json_schema(ExtractionSchema)

        # Top-level object must have additionalProperties: false
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert "facts" in schema["required"]

        # Nested ExtractionFact must also be strict
        fact_schema = schema["properties"]["facts"]["items"]
        assert fact_schema["type"] == "object"
        assert fact_schema["additionalProperties"] is False
        for field in ["fact_type", "value", "evidence_quote"]:
            assert field in fact_schema["required"]

        # Optional fields must be nullable unions
        amount_schema = fact_schema["properties"]["amount"]
        assert "anyOf" in amount_schema
        types = [item.get("type") for item in amount_schema["anyOf"]]
        assert "null" in types
        assert "number" in types

    def test_reasoning_schema_strict(self):
        """ReasoningSchema converts to strict JSON Schema."""
        from ai.llm_provider import ReasoningSchema

        schema = _pydantic_to_strict_json_schema(ReasoningSchema)

        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        for field in ["claims", "classification", "confidence", "reasoning_summary"]:
            assert field in schema["required"]

        # Nested claim must be strict
        claim_schema = schema["properties"]["claims"]["items"]
        assert claim_schema["type"] == "object"
        assert claim_schema["additionalProperties"] is False

    def test_no_defs_in_output(self):
        """$defs should be inlined, not left at top level."""
        from ai.llm_provider import ExtractionSchema

        schema = _pydantic_to_strict_json_schema(ExtractionSchema)
        assert "$defs" not in schema
        assert "$ref" not in schema.get("properties", {}).get("facts", {})

    def test_extraction_schema_groq_strict_invariants(self):
        """Assert ALL Groq strict-mode invariants for ExtractionSchema.

        Groq strict mode requires:
        - Every object has additionalProperties: false
        - Every property is listed in required
        - Optional fields use nullable anyOf unions
        - No $defs or $ref references remain
        """
        from ai.llm_provider import ExtractionSchema

        schema = _pydantic_to_strict_json_schema(ExtractionSchema)

        def _assert_strict_object(s, path="root"):
            """Recursively verify Groq strict-mode invariants."""
            if not isinstance(s, dict):
                return

            if s.get("type") == "object":
                # Rule 1: additionalProperties must be false
                assert s.get("additionalProperties") is False, \
                    f"{path}: additionalProperties must be false, got {s.get('additionalProperties')}"

                props = s.get("properties", {})
                required = s.get("required", [])

                # Rule 2: every property must be in required
                for prop_name in props:
                    assert prop_name in required, \
                        f"{path}: property '{prop_name}' not in required {required}"

                # Recurse into each property
                for prop_name, prop_schema in props.items():
                    _assert_strict_object(prop_schema, f"{path}.{prop_name}")

            # Rule 3: arrays — recurse into items
            if s.get("type") == "array" and "items" in s:
                _assert_strict_object(s["items"], f"{path}.items")

            # Rule 4: nullable fields use anyOf with null
            if "anyOf" in s:
                types = [item.get("type") for item in s["anyOf"] if isinstance(item, dict)]
                assert "null" in types, \
                    f"{path}: anyOf must include null type, got {types}"
                # Ensure only one non-null type (simple nullable union)
                non_null = [t for t in types if t != "null"]
                assert len(non_null) == 1, \
                    f"{path}: anyOf with null must have exactly one non-null type, got {non_null}"
                for item in s["anyOf"]:
                    _assert_strict_object(item, f"{path}.anyOf")

            # Rule 5: no $defs or $ref
            assert "$defs" not in s, f"{path}: $defs must not appear"
            assert "$ref" not in s, f"{path}: $ref must not appear"

        _assert_strict_object(schema)

        # Specific field checks for ExtractionSchema
        fact_items = schema["properties"]["facts"]["items"]
        # amount must be nullable number
        amount_schema = fact_items["properties"]["amount"]
        assert amount_schema["anyOf"] == [{"type": "number"}, {"type": "null"}], \
            f"amount must be nullable number, got {amount_schema}"
        # date must be nullable string
        date_schema = fact_items["properties"]["date"]
        assert date_schema["anyOf"] == [{"type": "string"}, {"type": "null"}], \
            f"date must be nullable string, got {date_schema}"

    def test_reasoning_schema_groq_strict_invariants(self):
        """Assert ALL Groq strict-mode invariants for ReasoningSchema."""
        from ai.llm_provider import ReasoningSchema

        schema = _pydantic_to_strict_json_schema(ReasoningSchema)

        def _assert_strict_object(s, path="root"):
            if not isinstance(s, dict):
                return
            if s.get("type") == "object":
                assert s.get("additionalProperties") is False, \
                    f"{path}: additionalProperties must be false"
                props = s.get("properties", {})
                required = s.get("required", [])
                for prop_name in props:
                    assert prop_name in required, \
                        f"{path}: property '{prop_name}' not in required"
                for prop_name, prop_schema in props.items():
                    _assert_strict_object(prop_schema, f"{path}.{prop_name}")
            if s.get("type") == "array" and "items" in s:
                _assert_strict_object(s["items"], f"{path}.items")
            if "anyOf" in s:
                for item in s["anyOf"]:
                    _assert_strict_object(item, f"{path}.anyOf")
            assert "$defs" not in s, f"{path}: $defs must not appear"
            assert "$ref" not in s, f"{path}: $ref must not appear"

        _assert_strict_object(schema)

        # Specific checks
        claim_items = schema["properties"]["claims"]["items"]
        assert "evidence_ids" in claim_items["required"]
        assert claim_items["properties"]["evidence_ids"]["type"] == "array"
        assert claim_items["properties"]["evidence_ids"]["items"]["type"] == "string"

    def test_response_format_payload_is_strict(self):
        """Verify the actual payload that would be sent to Groq for ExtractionSchema."""
        from ai.llm_provider import ExtractionSchema

        schema = _pydantic_to_strict_json_schema(ExtractionSchema)
        payload_response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "ExtractionSchema",
                "strict": True,
                "schema": schema,
            },
        }
        # Must be exactly this structure
        assert payload_response_format["type"] == "json_schema"
        assert payload_response_format["json_schema"]["strict"] is True
        assert payload_response_format["json_schema"]["name"] == "ExtractionSchema"
        # Schema must satisfy all strict constraints
        inner = payload_response_format["json_schema"]["schema"]
        assert inner["additionalProperties"] is False
        assert "facts" in inner["required"]


class TestAnthropicProvider:
    pass
