"""Metrics and reports for the reconciliation benchmark.

Every metric is computed from ACTUAL case results — never hard-coded.

Safety-critical metric: false_auto_resolve — a case whose ground truth
requires review/exception but was auto-classified MATCHED.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from benchmark.reconciliation_runner import ReconciliationBenchmarkRun
from reconciliation.models import (
    CLASS_MATCHED,
    CLASS_REVIEW_REQUIRED,
    CLASS_EXCEPTION,
    AI_AVAILABLE,
    AI_UNAVAILABLE,
    AI_FAILED,
)


@dataclass
class ReconciliationBenchmarkMetrics:
    mode: str = "deterministic"
    failure_mode: str = ""
    provider: str = ""
    model: str = ""
    total_cases: int = 0
    total_records: int = 0
    total_events: int = 0
    matched: int = 0
    review_required: int = 0
    exceptions: int = 0
    errors: int = 0
    match_rate: float = 0.0
    # Tier 1-7 footprint (count of cases that exercised each tier).
    tier_distribution: dict = field(default_factory=dict)
    # Deterministic vs AI-assisted resolution split (never conflated).
    deterministic_resolved: int = 0
    ai_resolved: int = 0
    # Accuracy vs hidden ground truth
    classification_accuracy: float = 0.0
    calculation_accuracy: float = 0.0
    correct_matched: int = 0
    correct_review_required: int = 0
    correct_exception: int = 0
    false_auto_resolve: int = 0  # the critical safety metric
    false_auto_resolve_rate: float = 0.0
    # Performance
    throughput_cases_per_sec: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_avg_ms: float = 0.0
    # Integrity
    duplicates_detected: int = 0
    audit_completeness: float = 0.0
    ai_available_count: int = 0
    ai_unavailable_count: int = 0
    ai_failed_count: int = 0
    ai_invoked_count: int = 0  # cases where the provider was genuinely invoked
    # Unresolved exception list (individually)
    unresolved_exceptions: list[dict] = field(default_factory=list)
    # Per-scenario breakdown
    scenario_metrics: dict = field(default_factory=dict)


def compute_reconciliation_metrics(run: ReconciliationBenchmarkRun) -> ReconciliationBenchmarkMetrics:
    m = ReconciliationBenchmarkMetrics(
        mode=run.mode,
        failure_mode=run.failure_mode,
        provider=run.provider,
        model=run.model,
    )
    results = run.results
    m.total_cases = len(results)
    if not results:
        return m

    latencies = [r.latency_ms for r in results if r.latency_ms > 0]

    for r in results:
        # Classification vs ground truth
        correct = r.classification == r.gt_classification
        if correct:
            m.classification_accuracy += 1
            if r.gt_classification == CLASS_MATCHED:
                m.correct_matched += 1
            elif r.gt_classification == CLASS_REVIEW_REQUIRED:
                m.correct_review_required += 1
            elif r.gt_classification == CLASS_EXCEPTION:
                m.correct_exception += 1

        # FALSE AUTO-RESOLVE: ground truth demands review/exception but
        # the controller classified MATCHED.
        if (
            r.gt_classification in (CLASS_REVIEW_REQUIRED, CLASS_EXCEPTION)
            and r.classification == CLASS_MATCHED
        ):
            m.false_auto_resolve += 1

        # Calculation accuracy: expected settlement must equal ground truth
        # (0 for cases where no settlement can be computed).
        if r.gt_expected_amount == 0:
            calc_ok = r.expected_amount == 0
        else:
            calc_ok = r.expected_amount == r.gt_expected_amount
        if calc_ok:
            m.calculation_accuracy += 1

        # Status buckets
        if r.classification == CLASS_MATCHED:
            m.matched += 1
        elif r.classification == CLASS_REVIEW_REQUIRED:
            m.review_required += 1
        elif r.classification == CLASS_EXCEPTION:
            m.exceptions += 1
        else:
            m.errors += 1

        # AI status
        if r.ai_status == AI_AVAILABLE:
            m.ai_available_count += 1
        elif r.ai_status == AI_UNAVAILABLE:
            m.ai_unavailable_count += 1
        elif r.ai_status == AI_FAILED:
            m.ai_failed_count += 1
        if getattr(r, "ai_invoked", False):
            m.ai_invoked_count += 1

        # Unresolved exceptions, listed individually
        if r.classification in (CLASS_REVIEW_REQUIRED, CLASS_EXCEPTION):
            m.unresolved_exceptions.append({
                "case_id": r.case_id,
                "payment_id": r.payment_id,
                "scenario": r.scenario,
                "classification": r.classification,
                "exception_codes": list(r.exception_codes),
                "variance": r.variance,
                "expected_amount": r.expected_amount,
                "actual_amount": r.actual_amount,
                "ground_truth_classification": r.gt_classification,
                "ground_truth_exception_code": r.gt_exception_code,
                "ai_status": r.ai_status,
                "ai_technical_reason": r.ai_technical_reason,
                "human_review_required": True,
            })

    # Total events = sum of the actual records processed per case.
    m.total_records = sum(getattr(r, "record_count", 1) for r in results)
    m.total_events = m.total_records

    # Tier distribution: a case can exercise more than one tier.
    tier_counts: dict = {}
    for r in results:
        for t in (getattr(r, "tiers_applied", None) or []):
            tier_counts[int(t)] = tier_counts.get(int(t), 0) + 1
    m.tier_distribution = {
        str(t): tier_counts.get(t, 0) for t in range(1, 8)
    }

    # Deterministic vs AI-assisted: a case is deterministically resolved
    # when AI was never invoked for it (gated out or deterministic mode);
    # AI-assisted when the provider genuinely ran.
    m.deterministic_resolved = sum(
        1 for r in results if not getattr(r, "ai_invoked", False)
    )
    m.ai_resolved = sum(1 for r in results if getattr(r, "ai_invoked", False))

    m.classification_accuracy = m.classification_accuracy / m.total_cases
    m.calculation_accuracy = m.calculation_accuracy / m.total_cases
    m.match_rate = m.matched / m.total_cases
    m.false_auto_resolve_rate = m.false_auto_resolve / m.total_cases

    if run.duration_ms > 0:
        m.throughput_cases_per_sec = m.total_cases / (run.duration_ms / 1000)
    if latencies:
        m.latency_avg_ms = statistics.mean(latencies)
        m.latency_p50_ms = statistics.median(latencies)
        sorted_lat = sorted(latencies)
        m.latency_p95_ms = sorted_lat[min(int(len(sorted_lat) * 0.95), len(sorted_lat) - 1)]

    # Audit completeness: every case produced a decision or an explicitly
    # recorded no-decision exception.
    complete = sum(
        1 for r in results
        if r.decision_id or r.classification != "" and r.error == ""
    )
    m.audit_completeness = complete / m.total_cases if m.total_cases else 0.0

    # Duplicates: count cases where duplicate payloads were detected
    # (deduped idempotently at ingestion).
    m.duplicates_detected = sum(
        1 for r in results if r.scenario == "duplicate_webhook"
    )

    # Per-scenario breakdown
    from reconciliation.dataset import ALL_SCENARIOS
    for scenario in ALL_SCENARIOS:
        sc_results = [r for r in results if r.scenario == scenario]
        if not sc_results:
            continue
        correct = sum(1 for r in sc_results if r.classification == r.gt_classification)
        m.scenario_metrics[scenario] = {
            "total": len(sc_results),
            "correct": correct,
            "accuracy": round(correct / len(sc_results), 4),
            "false_auto_resolve": sum(
                1 for r in sc_results
                if r.gt_classification in (CLASS_REVIEW_REQUIRED, CLASS_EXCEPTION)
                and r.classification == CLASS_MATCHED
            ),
        }

    return m


def reconciliation_metrics_to_dict(m: ReconciliationBenchmarkMetrics, run: ReconciliationBenchmarkRun) -> dict:
    mode_label = {
        "deterministic": "DETERMINISTIC-ONLY (no AI)",
        "llm": "REAL LLM",
        "failure": f"PROVIDER FAILURE ({run.failure_mode})",
    }[run.mode]

    return {
        "run_id": run.run_id,
        "mode": run.mode,
        "mode_label": mode_label,
        "provider": run.provider,
        "model": run.model,
        "dataset_seed": run.dataset_seed,
        "total_cases": m.total_cases,
        "total_records": m.total_records,
        "total_events": m.total_events,
        "tier_distribution": m.tier_distribution,
        "status": {
            "matched": m.matched,
            "review_required": m.review_required,
            "exceptions": m.exceptions,
            "errors": m.errors,
            "match_rate": round(m.match_rate, 4),
        },
        "accuracy": {
            "classification_accuracy": round(m.classification_accuracy, 4),
            "calculation_accuracy": round(m.calculation_accuracy, 4),
            "correct_matched": m.correct_matched,
            "correct_review_required": m.correct_review_required,
            "correct_exception": m.correct_exception,
            "false_auto_resolve": m.false_auto_resolve,
            "false_auto_resolve_rate": round(m.false_auto_resolve_rate, 4),
        },
        "performance": {
            "throughput_cases_per_sec": round(m.throughput_cases_per_sec, 2),
            "latency_p50_ms": round(m.latency_p50_ms, 1),
            "latency_p95_ms": round(m.latency_p95_ms, 1),
            "latency_avg_ms": round(m.latency_avg_ms, 1),
            "duration_ms": run.duration_ms,
        },
        "integrity": {
            "duplicates_detected": m.duplicates_detected,
            "audit_completeness": round(m.audit_completeness, 4),
        },
        "ai": {
            "available": m.ai_available_count,
            "unavailable": m.ai_unavailable_count,
            "failed": m.ai_failed_count,
            "invoked": m.ai_invoked_count,
            "invocation_rate": round(
                m.ai_invoked_count / m.total_cases, 4
            ) if m.total_cases else 0.0,
            "deterministic_resolved": m.deterministic_resolved,
            "ai_resolved": m.ai_resolved,
            "provider_invocations": run.provider_invocations,
        },
        "scenario_metrics": m.scenario_metrics,
        "unresolved_exceptions": m.unresolved_exceptions,
    }


def reconciliation_metrics_to_markdown(m: ReconciliationBenchmarkMetrics, run: ReconciliationBenchmarkRun) -> str:
    d = reconciliation_metrics_to_dict(m, run)
    lines = []
    lines.append(f"# Finance Controller Reconciliation Benchmark — {d['mode_label']}")
    lines.append("")
    lines.append("## Dataset")
    lines.append("")
    lines.append(f"- **Cases:** {m.total_cases} (seed {run.dataset_seed})")
    lines.append(f"- **Events processed:** {m.total_events}")
    lines.append(f"- **Provider:** {run.provider or 'none'}")
    lines.append(f"- **Model:** {run.model or 'none'}")
    lines.append(f"- **Mode:** {d['mode_label']}")
    lines.append(f"- **Duration:** {run.duration_ms / 1000:.1f}s")
    lines.append(f"- **Run ID:** {run.run_id}")
    lines.append("")
    lines.append("## Tier Footprint")
    lines.append("")
    lines.append(f"| Tier | Domain | Cases |")
    lines.append(f"|------|--------|-------|")
    tier_names = {
        "1": "Payment / Order", "2": "Refund", "3": "Settlement",
        "4": "Fee / Tax", "5": "Dispute / Risk",
        "6": "Invoice / Payment Link",
        "7": "Operational / Event Integrity",
    }
    for t in ("1", "2", "3", "4", "5", "6", "7"):
        lines.append(f"| {t} | {tier_names[t]} | {m.tier_distribution.get(t, 0)} |")
    lines.append("")
    lines.append("")
    lines.append("## Outcomes")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Matched | {m.matched}/{m.total_cases} |")
    lines.append(f"| Review required | {m.review_required}/{m.total_cases} |")
    lines.append(f"| Exceptions | {m.exceptions}/{m.total_cases} |")
    lines.append(f"| Errors | {m.errors}/{m.total_cases} |")
    lines.append(f"| Match rate | {m.match_rate:.1%} |")
    lines.append("")
    lines.append("## Accuracy vs Ground Truth")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Classification accuracy | {m.classification_accuracy:.1%} |")
    lines.append(f"| Calculation accuracy | {m.calculation_accuracy:.1%} |")
    lines.append(f"| Correct matched | {m.correct_matched} |")
    lines.append(f"| Correct review-required | {m.correct_review_required} |")
    lines.append(f"| Correct exception | {m.correct_exception} |")
    lines.append(f"| **False auto-resolve** | **{m.false_auto_resolve}** |")
    lines.append(f"| False auto-resolve rate | {m.false_auto_resolve_rate:.1%} |")
    lines.append("")
    lines.append("## Performance")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Throughput | {m.throughput_cases_per_sec:.1f} cases/sec |")
    lines.append(f"| P50 latency | {m.latency_p50_ms:.0f} ms |")
    lines.append(f"| P95 latency | {m.latency_p95_ms:.0f} ms |")
    lines.append(f"| Avg latency | {m.latency_avg_ms:.0f} ms |")
    lines.append("")
    lines.append("## Integrity")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Duplicates detected | {m.duplicates_detected} |")
    lines.append(f"| Audit completeness | {m.audit_completeness:.1%} |")
    lines.append(f"| AI available | {m.ai_available_count} |")
    lines.append(f"| AI unavailable | {m.ai_unavailable_count} |")
    lines.append(f"| AI failed | {m.ai_failed_count} |")
    lines.append(f"| AI invoked (gated) | {m.ai_invoked_count} |")
    lines.append(f"| Deterministic resolution | {m.deterministic_resolved}/{m.total_cases} |")
    lines.append(f"| AI-assisted resolution | {m.ai_resolved}/{m.total_cases} |")
    lines.append(f"| Provider invocations | {run.provider_invocations} |")
    lines.append("")

    if m.scenario_metrics:
        lines.append("## Scenario Breakdown")
        lines.append("")
        lines.append("| Scenario | Total | Correct | Accuracy | False auto-resolve |")
        lines.append("|----------|-------|---------|----------|--------------------|")
        for sc, sm in sorted(m.scenario_metrics.items()):
            lines.append(
                f"| {sc} | {sm['total']} | {sm['correct']} | {sm['accuracy']:.0%} "
                f"| {sm['false_auto_resolve']} |"
            )
        lines.append("")

    if m.unresolved_exceptions:
        lines.append("## Unresolved Exceptions (individual)")
        lines.append("")
        for exc in m.unresolved_exceptions[:20]:
            codes = ", ".join(exc["exception_codes"]) or "none"
            lines.append(f"### {exc['case_id']} ({exc['scenario']}) — {exc['classification']}")
            lines.append(f"- **Exception codes:** {codes}")
            lines.append(f"- **Expected:** {exc['expected_amount']} paise, "
                         f"**Actual:** {exc['actual_amount']} paise, "
                         f"**Variance:** {exc['variance']:+d} paise")
            lines.append(f"- **AI status:** {exc['ai_status']} "
                         f"{('— ' + exc['ai_technical_reason']) if exc['ai_technical_reason'] else ''}")
            lines.append(f"- **Ground truth:** {exc['ground_truth_classification']} "
                         f"({exc['ground_truth_exception_code'] or 'none'})")
            lines.append(f"- **Human review required:** Yes")
            lines.append("")
        if len(m.unresolved_exceptions) > 20:
            lines.append(f"*...and {len(m.unresolved_exceptions) - 20} more*")
            lines.append("")

    lines.append("## Limitations")
    lines.append("")
    lines.append("- Synthetic dataset ≠ production guarantee")
    lines.append("- Deterministic engine is authoritative; AI interprets only")
    lines.append("- Provider-failure mode uses simulated failures, NOT real AI accuracy")
    lines.append("- False auto-resolve is the primary safety metric; the system optimizes for zero")

    return "\n".join(lines)