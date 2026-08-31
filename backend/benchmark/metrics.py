"""Metrics calculator and report generator for benchmark results.

Produces both machine-readable JSON and human-readable Markdown.
"""
import json
import statistics
from dataclasses import dataclass, field, asdict
from typing import Optional

from benchmark.runner import BenchmarkRun, CaseResult
from benchmark.generator import ALL_CATEGORIES


@dataclass
class BenchmarkMetrics:
    """Aggregate metrics for a benchmark run."""
    total_cases: int = 0
    # Classification
    correct_classification: int = 0
    incorrect_classification: int = 0
    classification_accuracy: float = 0.0
    # Decision status
    correct_approved: int = 0
    correct_review_required: int = 0
    correct_exception: int = 0
    false_auto_resolution: int = 0  # primary safety metric
    # Legacy compatibility
    correct_decisions: int = 0
    review_required: int = 0
    exceptions: int = 0
    match_rate: float = 0.0
    auto_resolution_rate: float = 0.0
    review_rate: float = 0.0
    exception_rate: float = 0.0
    false_auto_resolution_rate: float = 0.0
    # Financial
    calculation_accuracy: float = 0.0
    calc_exact_matches: int = 0
    calc_total: int = 0
    # Performance
    throughput_cases_per_sec: float = 0.0
    total_duration_ms: int = 0
    latency_avg_ms: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    # Integrity
    duplicate_decisions: int = 0
    audit_completeness: float = 0.0
    # Agent metrics
    avg_tool_calls: float = 0.0
    avg_iterations: float = 0.0
    pct_requiring_tools: float = 0.0
    pct_resolving_without_tools: float = 0.0
    tool_success_rate: float = 0.0
    agent_failure_rate: float = 0.0
    max_tool_calls: int = 0
    max_iterations: int = 0
    # Per-category breakdown
    category_metrics: dict = field(default_factory=dict)
    # Exception list
    exception_details: list = field(default_factory=list)


def compute_metrics(run: BenchmarkRun) -> BenchmarkMetrics:
    """Compute aggregate metrics from a benchmark run."""
    results = run.case_results
    if not results:
        return BenchmarkMetrics()

    m = BenchmarkMetrics()
    m.total_cases = len(results)
    m.total_duration_ms = run.duration_ms

    # Classification + decision status
    for r in results:
        cls_match = r.actual_classification == r.expected_classification

        if cls_match:
            m.correct_classification += 1
        else:
            m.incorrect_classification += 1

        # Determine expected and actual decision status
        expected_needs_review = r.expected_classification in ("exception", "ambiguous")
        actual_approved = r.status in ("APPROVED", "completed")

        if actual_approved and not expected_needs_review:
            # Correctly auto-resolved (expected clear, got approved)
            m.correct_approved += 1
            m.correct_decisions += 1
        elif actual_approved and expected_needs_review:
            # FALSE AUTO-RESOLUTION — most dangerous case
            m.false_auto_resolution += 1
        elif r.status == "REVIEW_REQUIRED":
            m.correct_review_required += 1
            m.review_required += 1
            if cls_match:
                m.correct_decisions += 1
        elif r.status == "exception" or (r.exception_reason and r.status != "REVIEW_REQUIRED"):
            m.correct_exception += 1
            m.exceptions += 1
        else:
            m.exceptions += 1

    m.classification_accuracy = m.correct_classification / m.total_cases if m.total_cases else 0
    m.match_rate = m.correct_decisions / m.total_cases if m.total_cases else 0
    m.auto_resolution_rate = m.correct_approved / m.total_cases if m.total_cases else 0
    m.review_rate = m.review_required / m.total_cases if m.total_cases else 0
    m.exception_rate = m.exceptions / m.total_cases if m.total_cases else 0
    m.false_auto_resolution_rate = m.false_auto_resolution / m.total_cases if m.total_cases else 0

    # Financial calculation accuracy
    m.calc_exact_matches = sum(1 for r in results if r.calculation_match)
    m.calc_total = sum(1 for r in results if r.actual_final_amount > 0)
    m.calculation_accuracy = m.calc_exact_matches / m.calc_total if m.calc_total else 0

    # Throughput
    if run.duration_ms > 0:
        m.throughput_cases_per_sec = m.total_cases / (run.duration_ms / 1000)

    # Latency
    latencies = [r.latency_ms for r in results if r.latency_ms > 0]
    if latencies:
        m.latency_avg_ms = statistics.mean(latencies)
        m.latency_p50_ms = statistics.median(latencies)
        sorted_lat = sorted(latencies)
        p95_idx = int(len(sorted_lat) * 0.95)
        p99_idx = int(len(sorted_lat) * 0.99)
        m.latency_p95_ms = sorted_lat[min(p95_idx, len(sorted_lat) - 1)]
        m.latency_p99_ms = sorted_lat[min(p99_idx, len(sorted_lat) - 1)]

    # Duplicate detection
    decision_ids = [r.decision_id for r in results if r.decision_id]
    m.duplicate_decisions = len(decision_ids) - len(set(decision_ids))

    # Audit completeness
    complete = sum(
        1 for r in results
        if r.decision_id and r.actual_final_amount > 0 and r.status
    )
    m.audit_completeness = complete / m.total_cases if m.total_cases else 0

    # Agent metrics
    tool_counts = [r.tool_calls for r in results]
    iter_counts = [r.agent_iterations for r in results]
    m.avg_tool_calls = statistics.mean(tool_counts) if tool_counts else 0
    m.avg_iterations = statistics.mean(iter_counts) if iter_counts else 0
    m.max_tool_calls = max(tool_counts) if tool_counts else 0
    m.max_iterations = max(iter_counts) if iter_counts else 0
    requiring_tools = sum(1 for r in results if r.tool_calls > 0)
    m.pct_requiring_tools = requiring_tools / m.total_cases if m.total_cases else 0
    m.pct_resolving_without_tools = 1 - m.pct_requiring_tools
    successes = sum(1 for r in results if r.agent_success)
    m.tool_success_rate = successes / m.total_cases if m.total_cases else 0
    m.agent_failure_rate = 1 - m.tool_success_rate

    # Per-category breakdown
    for cat in ALL_CATEGORIES:
        cat_results = [r for r in results if r.category == cat]
        if not cat_results:
            continue
        cat_correct = sum(1 for r in cat_results if r.status in ("APPROVED", "completed"))
        cat_review = sum(1 for r in cat_results if r.status == "REVIEW_REQUIRED")
        cat_exc = sum(1 for r in cat_results if r.status == "exception" or r.exception_reason)
        m.category_metrics[cat] = {
            "total": len(cat_results),
            "correct": cat_correct,
            "review_required": cat_review,
            "exceptions": cat_exc,
            "match_rate": cat_correct / len(cat_results),
        }

    # Exception details
    for r in results:
        if r.exception_reason or r.status == "exception":
            m.exception_details.append({
                "case_id": r.case_id,
                "category": r.category,
                "expected_classification": r.expected_classification,
                "actual_classification": r.actual_classification,
                "expected_final_amount": r.expected_final_amount,
                "actual_final_amount": r.actual_final_amount,
                "exception_reason": r.exception_reason or "Classification mismatch",
                "decision_id": r.decision_id,
                "human_review_required": True,
            })
        elif r.expected_classification != r.actual_classification:
            m.exception_details.append({
                "case_id": r.case_id,
                "category": r.category,
                "expected_classification": r.expected_classification,
                "actual_classification": r.actual_classification,
                "expected_final_amount": r.expected_final_amount,
                "actual_final_amount": r.actual_final_amount,
                "exception_reason": f"Classification mismatch: expected {r.expected_classification}, got {r.actual_classification}",
                "decision_id": r.decision_id,
                "human_review_required": r.actual_classification == "exception",
            })

    return m


def metrics_to_json(metrics: BenchmarkMetrics, run: BenchmarkRun) -> dict:
    """Convert metrics to machine-readable JSON."""
    return {
        "run_id": run.run_id,
        "dataset_seed": run.dataset_seed,
        "dataset_version": run.dataset_version,
        "benchmark_version": run.benchmark_version,
        "model": run.model,
        "provider": run.provider,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "total_cases": metrics.total_cases,
        "classification": {
            "correct": metrics.correct_classification,
            "incorrect": metrics.incorrect_classification,
            "accuracy": round(metrics.classification_accuracy, 4),
        },
        "decision": {
            "correctly_approved": metrics.correct_approved,
            "correctly_review_required": metrics.correct_review_required,
            "correctly_exception": metrics.correct_exception,
            "false_auto_resolution": metrics.false_auto_resolution,
            "false_auto_resolution_rate": round(metrics.false_auto_resolution_rate, 4),
        },
        "financial": {
            "exact_matches": metrics.calc_exact_matches,
            "total_calculated": metrics.calc_total,
            "calculation_accuracy": round(metrics.calculation_accuracy, 4),
        },
        "rates": {
            "match_rate": round(metrics.match_rate, 4),
            "auto_resolution_rate": round(metrics.auto_resolution_rate, 4),
            "review_rate": round(metrics.review_rate, 4),
            "exception_rate": round(metrics.exception_rate, 4),
            "calculation_accuracy": round(metrics.calculation_accuracy, 4),
            "audit_completeness": round(metrics.audit_completeness, 4),
        },
        "performance": {
            "throughput_cases_per_sec": round(metrics.throughput_cases_per_sec, 2),
            "total_duration_ms": metrics.total_duration_ms,
            "latency_avg_ms": round(metrics.latency_avg_ms, 1),
            "latency_p50_ms": round(metrics.latency_p50_ms, 1),
            "latency_p95_ms": round(metrics.latency_p95_ms, 1),
            "latency_p99_ms": round(metrics.latency_p99_ms, 1),
        },
        "agent": {
            "avg_tool_calls": round(metrics.avg_tool_calls, 2),
            "avg_iterations": round(metrics.avg_iterations, 2),
            "pct_requiring_tools": round(metrics.pct_requiring_tools, 4),
            "pct_resolving_without_tools": round(metrics.pct_resolving_without_tools, 4),
            "tool_success_rate": round(metrics.tool_success_rate, 4),
            "agent_failure_rate": round(metrics.agent_failure_rate, 4),
            "max_tool_calls": metrics.max_tool_calls,
            "max_iterations": metrics.max_iterations,
        },
        "idempotency": {
            "duplicate_decisions": metrics.duplicate_decisions,
        },
        "category_metrics": metrics.category_metrics,
        "exceptions": metrics.exception_details,
    }


def metrics_to_markdown(metrics: BenchmarkMetrics, run: BenchmarkRun) -> str:
    """Convert metrics to human-readable Markdown report."""
    mode_label = "Real AI Benchmark" if run.provider != "mock" else "Mock / Regression Benchmark"
    lines = []
    lines.append(f"# Finance Controller {mode_label}")
    lines.append("")
    lines.append("## Dataset")
    lines.append("")
    lines.append(f"- **Cases:** {metrics.total_cases}")
    lines.append(f"- **Seed:** {run.dataset_seed}")
    lines.append(f"- **Provider:** {run.provider}")
    lines.append(f"- **Model:** {run.model}")
    lines.append(f"- **Duration:** {run.duration_ms / 1000:.1f}s")
    lines.append(f"- **Run ID:** {run.run_id}")
    lines.append(f"- **Timestamp:** {run.started_at}")
    lines.append("")
    lines.append("## Classification")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Correct classification | {metrics.correct_classification}/{metrics.total_cases} |")
    lines.append(f"| Incorrect classification | {metrics.incorrect_classification}/{metrics.total_cases} |")
    lines.append(f"| **Classification accuracy** | **{metrics.classification_accuracy:.1%}** |")
    lines.append("")
    lines.append("## Decision Status")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Correctly approved | {metrics.correct_approved}/{metrics.total_cases} |")
    lines.append(f"| Correctly review-required | {metrics.correct_review_required}/{metrics.total_cases} |")
    lines.append(f"| Correctly exception | {metrics.correct_exception}/{metrics.total_cases} |")
    lines.append(f"| **False auto-resolution** | **{metrics.false_auto_resolution}/{metrics.total_cases}** |")
    lines.append(f"| False auto-resolution rate | {metrics.false_auto_resolution_rate:.1%} |")
    lines.append("")
    lines.append("## Financial")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Exact calculation matches | {metrics.calc_exact_matches}/{metrics.calc_total} |")
    lines.append(f"| **Calculation accuracy** | **{metrics.calculation_accuracy:.1%}** |")
    lines.append("")
    lines.append("## Integrity")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Audit completeness | {metrics.audit_completeness:.1%} |")
    lines.append(f"| Duplicate decisions | {metrics.duplicate_decisions} |")
    lines.append("")
    lines.append("## Performance")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Throughput | {metrics.throughput_cases_per_sec:.1f} cases/sec |")
    lines.append(f"| Avg latency | {metrics.latency_avg_ms:.0f} ms |")
    lines.append(f"| P50 latency | {metrics.latency_p50_ms:.0f} ms |")
    lines.append(f"| P95 latency | {metrics.latency_p95_ms:.0f} ms |")
    lines.append(f"| P99 latency | {metrics.latency_p99_ms:.0f} ms |")
    lines.append("")
    lines.append("## Agent Metrics")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Avg tool calls/case | {metrics.avg_tool_calls:.1f} |")
    lines.append(f"| Avg iterations/case | {metrics.avg_iterations:.1f} |")
    lines.append(f"| Requiring tools | {metrics.pct_requiring_tools:.1%} |")
    lines.append(f"| Resolving without tools | {metrics.pct_resolving_without_tools:.1%} |")
    lines.append(f"| Agent success rate | {metrics.tool_success_rate:.1%} |")
    lines.append(f"| Max tool calls | {metrics.max_tool_calls} |")
    lines.append(f"| Max iterations | {metrics.max_iterations} |")
    lines.append("")
    lines.append("## Category Breakdown")
    lines.append("")
    lines.append(f"| Category | Total | Correct | Review | Exceptions | Match Rate |")
    lines.append(f"|----------|-------|---------|--------|------------|------------|")
    for cat, cm in sorted(metrics.category_metrics.items()):
        lines.append(
            f"| {cat} | {cm['total']} | {cm['correct']} | {cm['review_required']} "
            f"| {cm['exceptions']} | {cm['match_rate']:.0%} |"
        )
    lines.append("")

    if metrics.exception_details:
        lines.append("## Exception Report")
        lines.append("")
        for exc in metrics.exception_details:
            lines.append(f"### {exc['case_id']} ({exc['category']})")
            lines.append(f"- **Expected:** {exc['expected_classification']}")
            lines.append(f"- **Actual:** {exc['actual_classification']}")
            lines.append(f"- **Expected amount:** ₹{exc['expected_final_amount']:,}")
            lines.append(f"- **Actual amount:** ₹{exc['actual_final_amount']:,}")
            lines.append(f"- **Reason:** {exc['exception_reason']}")
            lines.append(f"- **Decision ID:** {exc.get('decision_id', 'none')}")
            lines.append(f"- **Human review:** {'Yes' if exc['human_review_required'] else 'No'}")
            lines.append("")

    lines.append("## Limitations")
    lines.append("")
    lines.append("- Synthetic benchmark ≠ production guarantee")
    lines.append("- Benchmark accuracy ≠ financial correctness without controls")
    lines.append("- Unresolved cases are intentionally escalated for human review")
    lines.append("- Deterministic calculation is authoritative; AI handles evidence interpretation only")
    lines.append("- Mock mode tests pipeline correctness, not LLM reasoning quality")
    lines.append("")
    lines.append("## Reproducibility")
    lines.append("")
    lines.append(f"- **Dataset seed:** {run.dataset_seed}")
    lines.append(f"- **Dataset version:** {run.dataset_version}")
    lines.append(f"- **Benchmark version:** {run.benchmark_version}")
    lines.append(f"- **Model:** {run.model}")
    lines.append(f"- **Provider:** {run.provider}")
    lines.append(f"- **Timestamp:** {run.started_at}")

    return "\n".join(lines)
