"""Finance Controller Benchmark CLI.

Usage:
    python -m benchmark --records 100
    python -m benchmark --records 100 --seed 42
    python -m benchmark --records 100 --provider groq --model openai/gpt-oss-120b
"""
import argparse
import json
import logging
import os
import sys
import time

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def main():
    # Load .env inside main() — not at module level — to avoid leaking
    # API keys into the process environment during test collection.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
    except ImportError:
        pass
    parser = argparse.ArgumentParser(description="Finance Controller Benchmark")
    parser.add_argument("--records", type=int, default=100, help="Number of cases (default: 100)")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic seed (default: 42)")
    parser.add_argument("--provider", type=str, default="mock",
                        help="Provider: mock, groq, ollama, gemini (default: mock)")
    parser.add_argument("--model", type=str, default="",
                        help="Model name (default: provider-specific)")
    parser.add_argument("--output", type=str, default="benchmark_output", help="Output directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--mode", type=str, default="scenario",
                        help="Benchmark type: scenario (default) or reconciliation")
    parser.add_argument("--failure-mode", type=str, default="429",
                        help="For reconciliation failure mode: 429, 503, timeout, malformed, "
                             "tool_incompat, missing_key (default: 429)")
    args = parser.parse_args()

    if args.mode == "reconciliation":
        return run_reconciliation_benchmark_cli(args)

    use_mock = args.provider == "mock"
    provider_name = args.provider
    model_name = args.model

    # Validate provider requires API key
    if not use_mock:
        if provider_name == "groq":
            if not os.environ.get("GROQ_API_KEY"):
                print("ERROR: GROQ_API_KEY not set. Cannot run real provider benchmark.")
                print("Set GROQ_API_KEY environment variable or use --provider mock")
                return 1
            if not model_name:
                model_name = "openai/gpt-oss-120b"
        elif provider_name == "gemini":
            if not os.environ.get("GEMINI_API_KEY"):
                print("ERROR: GEMINI_API_KEY not set.")
                return 1
            if not model_name:
                model_name = "gemini-2.5-flash"
        elif provider_name == "ollama":
            if not model_name:
                model_name = os.environ.get("OLLAMA_MODEL", "qwen3.5:latest")
            base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            from ai.llm_provider import OllamaProvider
            ollama = OllamaProvider(base_url=base_url, model=model_name)
            if not ollama.is_available():
                print(f"ERROR: Ollama not available (model={model_name}, url={base_url}).")
                print("Start Ollama: ollama serve && ollama pull " + model_name)
                return 1

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from benchmark.runner import run_benchmark
    from benchmark.metrics import compute_metrics, metrics_to_json, metrics_to_markdown

    mode_label = f"REAL ({provider_name}/{model_name})" if not use_mock else "MOCK (deterministic)"
    print(f"\n{'='*60}")
    print(f"FINANCE CONTROLLER BENCHMARK")
    print(f"{'='*60}")
    print(f"Records:    {args.records}")
    print(f"Seed:       {args.seed}")
    print(f"Provider:   {provider_name}")
    print(f"Model:      {model_name or 'default'}")
    print(f"Mode:       {mode_label}")
    print(f"{'='*60}\n")

    run = run_benchmark(
        count=args.records,
        seed=args.seed,
        use_mock=use_mock,
        provider_override=provider_name if not use_mock else None,
        model_override=model_name if not use_mock else None,
    )

    metrics = compute_metrics(run)

    # Print summary
    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")
    print(f"Total cases:         {metrics.total_cases}")
    print(f"Classification acc:  {metrics.classification_accuracy:.1%}")
    print(f"Calc accuracy:       {metrics.calculation_accuracy:.1%}")
    print(f"Correct approved:    {metrics.correct_approved}")
    print(f"Correct review-req:  {metrics.correct_review_required}")
    print(f"Correct exception:   {metrics.correct_exception}")
    print(f"FALSE AUTO-RESOLVE:  {metrics.false_auto_resolution}")
    print(f"Throughput:          {metrics.throughput_cases_per_sec:.1f} cases/sec")
    print(f"P50 latency:         {metrics.latency_p50_ms:.0f} ms")
    print(f"P95 latency:         {metrics.latency_p95_ms:.0f} ms")
    print(f"Duplicates:          {metrics.duplicate_decisions}")
    print(f"Audit complete:      {metrics.audit_completeness:.1%}")
    print(f"{'='*60}\n")

    # Write outputs
    os.makedirs(args.output, exist_ok=True)

    json_path = os.path.join(args.output, "benchmark_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metrics_to_json(metrics, run), f, indent=2)
    print(f"JSON report: {json_path}")

    # Write case-level results first (before markdown which may fail)
    cases_path = os.path.join(args.output, "case_results.json")
    case_data = []
    for r in run.case_results:
        case_data.append({
            "case_id": r.case_id,
            "category": r.category,
            "decision_id": r.decision_id,
            "status": r.status,
            "expected_classification": r.expected_classification,
            "actual_classification": r.actual_classification,
            "expected_final_amount": r.expected_final_amount,
            "actual_final_amount": r.actual_final_amount,
            "calculation_match": r.calculation_match,
            "evidence_sufficiency": r.evidence_sufficiency,
            "tool_calls": r.tool_calls,
            "agent_iterations": r.agent_iterations,
            "latency_ms": r.latency_ms,
            "exception_reason": r.exception_reason,
        })
    with open(cases_path, "w", encoding="utf-8") as f:
        json.dump(case_data, f, indent=2)
    print(f"Case results: {cases_path}")

    # Write markdown report
    try:
        md_path = os.path.join(args.output, "finance-controller-benchmark.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(metrics_to_markdown(metrics, run))
        print(f"Markdown report: {md_path}")
    except Exception as e:
        print(f"Warning: markdown generation failed: {e}")

    return 0 if metrics.exception_rate < 0.10 else 1


def run_reconciliation_benchmark_cli(args) -> int:
    """Run the reconciliation benchmark (deterministic / llm / failure)."""
    import logging as _logging

    mode = "llm" if args.provider != "mock" else "deterministic"
    if args.provider == "failure":
        mode = "failure"

    _logging.basicConfig(
        level=_logging.DEBUG if args.verbose else _logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from benchmark.reconciliation_runner import run_reconciliation_benchmark
    from benchmark.reconciliation_metrics import (
        compute_reconciliation_metrics,
        reconciliation_metrics_to_dict,
        reconciliation_metrics_to_markdown,
    )

    mode_label = {
        "deterministic": "DETERMINISTIC-ONLY (no AI)",
        "llm": f"REAL LLM ({args.provider}/{args.model or 'default'})",
        "failure": f"PROVIDER FAILURE ({args.failure_mode})",
    }[mode]

    print(f"\n{'='*60}")
    print(f"FINANCE CONTROLLER RECONCILIATION BENCHMARK")
    print(f"{'='*60}")
    print(f"Records:    {args.records}")
    print(f"Seed:       {args.seed}")
    print(f"Mode:       {mode_label}")
    print(f"{'='*60}\n")

    run = run_reconciliation_benchmark(
        count=args.records,
        seed=args.seed,
        mode=mode,
        failure_mode=args.failure_mode,
    )
    metrics = compute_reconciliation_metrics(run)
    data = reconciliation_metrics_to_dict(metrics, run)

    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")
    print(f"Total cases:          {metrics.total_cases}")
    print(f"Matched:              {metrics.matched}")
    print(f"Review required:      {metrics.review_required}")
    print(f"Exceptions:           {metrics.exceptions}")
    print(f"Match rate:           {metrics.match_rate:.1%}")
    print(f"Classification acc:   {metrics.classification_accuracy:.1%}")
    print(f"Calculation acc:      {metrics.calculation_accuracy:.1%}")
    print(f"Correct matched:      {metrics.correct_matched}")
    print(f"Correct review-req:   {metrics.correct_review_required}")
    print(f"Correct exception:    {metrics.correct_exception}")
    print(f"FALSE AUTO-RESOLVE:   {metrics.false_auto_resolve}")
    print(f"Throughput:           {metrics.throughput_cases_per_sec:.1f} cases/sec")
    print(f"P50 latency:          {metrics.latency_p50_ms:.0f} ms")
    print(f"P95 latency:          {metrics.latency_p95_ms:.0f} ms")
    print(f"Duplicates:           {metrics.duplicates_detected}")
    print(f"Audit complete:       {metrics.audit_completeness:.1%}")
    print(f"AI available:         {metrics.ai_available_count}")
    print(f"AI unavailable/failed:{metrics.ai_unavailable_count + metrics.ai_failed_count}")
    print(f"Provider invocations: {run.provider_invocations}")
    print(f"{'='*60}\n")

    # Write outputs
    os.makedirs(args.output, exist_ok=True)

    json_path = os.path.join(args.output, "benchmark_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"JSON report: {json_path}")

    cases_path = os.path.join(args.output, "case_results.json")
    with open(cases_path, "w", encoding="utf-8") as f:
        json.dump([r.to_dict() for r in run.results], f, indent=2)
    print(f"Case results: {cases_path}")

    md_path = os.path.join(args.output, "finance-controller-benchmark.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(reconciliation_metrics_to_markdown(metrics, run))
    print(f"Markdown report: {md_path}")

    # Fail the run when false auto-resolution occurs (safety gate)
    return 0 if metrics.false_auto_resolve == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
