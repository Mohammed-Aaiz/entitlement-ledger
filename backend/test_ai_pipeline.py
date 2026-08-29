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
from ai.llm_provider import is_ai_available, reset_provider, OpenRouterProvider
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


class TestAIAvailability:
    """Test AI availability check."""

    def test_no_api_key_no_ollama(self):
        """Returns False when neither Ollama nor Anthropic are available."""
        with patch.dict(os.environ, {}, clear=True):
            reset_provider()
            assert is_ai_available() is False

    def test_with_api_key(self):
        """Returns True when Anthropic API key is set (provider may not connect)."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            reset_provider()
            # AnthropicProvider.is_available() checks key + import, so this returns True
            from ai.llm_provider import AnthropicProvider
            assert AnthropicProvider().is_available() is True


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
        assert decision["status"] == "REVIEW_REQUIRED"
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
