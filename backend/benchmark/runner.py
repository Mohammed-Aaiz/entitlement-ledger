"""Benchmark runner — orchestrates synthetic cases through the Finance Controller.

Supports both mock and live modes. Mock mode uses deterministic responses
for CI; live mode exercises the real Groq-backed agent.
"""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from benchmark.generator import SyntheticCase, generate_cases, POLICIES
from calculations import (
    build_line_items,
    calculate_final_amount,
    calculate_platform_fee,
    validate_calculation,
)

logger = logging.getLogger(__name__)


@dataclass
class CaseResult:
    """Result of processing a single benchmark case."""
    case_id: str
    category: str
    decision_id: Optional[str] = None
    status: str = "pending"
    expected_classification: str = ""
    actual_classification: str = ""
    expected_final_amount: int = 0
    actual_final_amount: int = 0
    calculation_match: bool = False
    evidence_sufficiency: str = "unknown"
    tool_calls: int = 0
    agent_iterations: int = 0
    latency_ms: int = 0
    exception_reason: Optional[str] = None
    agent_success: bool = True
    stop_reason: str = ""


@dataclass
class BenchmarkRun:
    """Complete benchmark run with all results."""
    run_id: str = field(default_factory=lambda: f"bench_{uuid.uuid4().hex[:12]}")
    dataset_seed: int = 42
    dataset_version: str = "1.0"
    benchmark_version: str = "1.0"
    model: str = "mock"
    provider: str = "mock"
    total_cases: int = 0
    started_at: str = ""
    completed_at: str = ""
    duration_ms: int = 0
    case_results: list = field(default_factory=list)


def run_benchmark(
    count: int = 100,
    seed: int = 42,
    use_mock: bool = True,
    tenant_id: str = "benchmark",
    provider_override: str = None,
    model_override: str = None,
) -> BenchmarkRun:
    """Run a complete benchmark synchronously.

    Args:
        count: Number of cases to generate.
        seed: Deterministic seed.
        use_mock: If True, use mocked AI responses (for CI).
        tenant_id: Tenant ID for benchmark runs.
        provider_override: Force a specific provider (e.g. 'groq', 'ollama', 'gemini').
        model_override: Force a specific model (e.g. 'openai/gpt-oss-120b').

    Returns:
        BenchmarkRun with all results and metrics.
    """
    provider_name = provider_override or ("mock" if use_mock else "groq")
    model_name = model_override or ("mock" if use_mock else "openai/gpt-oss-120b")

    # For live provider mode, set up the provider with overrides
    if not use_mock and provider_override:
        from ai.llm_provider import (
            reset_provider, get_provider_by_name, _provider_instance,
        )
        import ai.llm_provider as llm_mod
        provider = get_provider_by_name(provider_override)
        # Override model if specified
        if model_override and hasattr(provider, 'model'):
            provider.model = model_override
        # Cache the provider so agent uses it
        llm_mod._provider_instance = provider
        logger.info("Benchmark provider set: %s/%s", provider_override, model_name)

    run = BenchmarkRun(
        dataset_seed=seed,
        total_cases=count,
        model=model_name,
        provider=provider_name,
    )
    run.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    cases = generate_cases(count=count, seed=seed)
    logger.info("Generated %d benchmark cases (seed=%d)", len(cases), seed)

    start = time.time()
    for i, case in enumerate(cases):
        case_start = time.time()
        result = _process_case(case, tenant_id, use_mock)
        result.latency_ms = int((time.time() - case_start) * 1000)
        run.case_results.append(result)
        if (i + 1) % 10 == 0:
            logger.info("Processed %d/%d cases", i + 1, len(cases))

    run.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run.duration_ms = int((time.time() - start) * 1000)

    return run


def _process_case(
    case: SyntheticCase,
    tenant_id: str,
    use_mock: bool,
) -> CaseResult:
    """Process a single benchmark case through the finance controller."""
    result = CaseResult(
        case_id=case.case_id,
        category=case.category,
        expected_classification=case.expected_classification,
        expected_final_amount=case.expected_final_amount,
    )

    try:
        if use_mock:
            _process_case_mock(case, result, tenant_id)
        else:
            _process_case_live(case, result, tenant_id)
    except Exception as e:
        result.status = "exception"
        result.exception_reason = f"{type(e).__name__}: {str(e)}"
        result.agent_success = False
        logger.error("Case %s failed: %s", case.case_id, str(e))

    # Validate calculation
    if result.actual_final_amount > 0:
        result.calculation_match = result.actual_final_amount == case.expected_final_amount

    return result


def _process_case_mock(
    case: SyntheticCase,
    result: CaseResult,
    tenant_id: str,
):
    """Process a case using deterministic mock AI responses."""
    from ai.agent import run_agent, AgentRunState
    from ai.pipeline import run_pipeline

    policies = [POLICIES[pid] for pid in case.applicable_policy_ids if pid in POLICIES]

    # Run agent with mock
    agent_result = asyncio.get_event_loop().run_until_complete(
        run_agent(
            tenant_id=tenant_id,
            scenario_id=case.case_id,
            entity_id=f"seller_{case.case_id}",
            gross_amount=case.gross_amount,
            evidence_records=case.evidence_records,
            policy_records=policies,
            scenario_description=f"Benchmark case: {case.category}",
            use_mock=True,
        )
    )

    # Extract agent metrics
    state = agent_result["agent_state"]
    result.tool_calls = len(state.tools_called)
    result.agent_iterations = state.iteration_count
    result.agent_success = state.success
    result.stop_reason = state.stop_reason

    # Get classification from analysis
    analysis = agent_result["analysis"]
    result.actual_classification = analysis.get("classification", "exception")

    # Run deterministic pipeline
    prev_hash = "genesis"
    pipeline_result = run_pipeline(
        scenario_id=case.case_id,
        evidence_records=case.evidence_records,
        policy_records=policies,
        prev_decision_hash=prev_hash,
        use_mock=False,
        agent_result=agent_result,
    )

    decision = pipeline_result["decision"]
    result.decision_id = decision["decision_id"]
    result.actual_final_amount = decision["final_amount"]
    result.status = decision["status"]

    # Evidence sufficiency
    if case.missing_delivery_evidence:
        result.evidence_sufficiency = "missing_delivery"
    elif case.conflicting_evidence:
        result.evidence_sufficiency = "conflicting"
    else:
        result.evidence_sufficiency = "sufficient"


def _process_case_live(
    case: SyntheticCase,
    result: CaseResult,
    tenant_id: str,
):
    """Process a case using the real LLM-backed agent."""
    from ai.agent import run_agent
    from ai.pipeline import run_pipeline

    policies = [POLICIES[pid] for pid in case.applicable_policy_ids if pid in POLICIES]

    agent_result = asyncio.get_event_loop().run_until_complete(
        run_agent(
            tenant_id=tenant_id,
            scenario_id=case.case_id,
            entity_id=f"seller_{case.case_id}",
            gross_amount=case.gross_amount,
            evidence_records=case.evidence_records,
            policy_records=policies,
            scenario_description=f"Benchmark case: {case.category}",
            use_mock=False,
        )
    )

    state = agent_result["agent_state"]
    result.tool_calls = len(state.tools_called)
    result.agent_iterations = state.iteration_count
    result.agent_success = state.success
    result.stop_reason = state.stop_reason

    analysis = agent_result["analysis"]
    result.actual_classification = analysis.get("classification", "exception")

    pipeline_result = run_pipeline(
        scenario_id=case.case_id,
        evidence_records=case.evidence_records,
        policy_records=policies,
        prev_decision_hash="genesis",
        use_mock=False,
        agent_result=agent_result,
    )

    decision = pipeline_result["decision"]
    result.decision_id = decision["decision_id"]
    result.actual_final_amount = decision["final_amount"]
    result.status = decision["status"]

    if case.missing_delivery_evidence:
        result.evidence_sufficiency = "missing_delivery"
    elif case.conflicting_evidence:
        result.evidence_sufficiency = "conflicting"
    else:
        result.evidence_sufficiency = "sufficient"
