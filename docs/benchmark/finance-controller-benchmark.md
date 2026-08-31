# Finance Controller Benchmark Report

## Dataset

- **Cases:** 100
- **Seed:** 42
- **Model:** mock (mock)
- **Duration:** 0.2s
- **Run ID:** bench_b3c5cde8787d

## Results Summary

| Metric | Value |
|--------|-------|
| Correct decisions | 85/100 |
| Review required | 15/100 |
| Exceptions | 0/100 |
| **Match rate** | **85.0%** |
| Auto-resolution rate | 85.0% |
| Financial calculation accuracy | 90.0% |
| Audit completeness | 100.0% |
| Duplicate decisions | 0 |

## Performance

| Metric | Value |
|--------|-------|
| Throughput | 434.8 cases/sec |
| Avg latency | 10 ms |
| P50 latency | 1 ms |
| P95 latency | 1 ms |
| P99 latency | 182 ms |

## Agent Metrics

| Metric | Value |
|--------|-------|
| Avg tool calls/case | 1.6 |
| Avg iterations/case | 2.6 |
| Requiring tools | 80.0% |
| Resolving without tools | 20.0% |
| Agent success rate | 100.0% |
| Max tool calls | 2 |
| Max iterations | 3 |

## Category Breakdown

| Category | Total | Correct | Review | Exceptions | Match Rate |
|----------|-------|---------|--------|------------|------------|
| clean_payment | 20 | 0 | 20 | 0 | 0% |
| conflicting_evidence | 5 | 0 | 5 | 0 | 0% |
| delivered_on_time | 15 | 0 | 15 | 0 | 0% |
| duplicate_event | 5 | 0 | 5 | 0 | 0% |
| fee_mismatch | 5 | 0 | 5 | 0 | 0% |
| late_delivery | 15 | 0 | 15 | 0 | 0% |
| missing_delivery | 5 | 0 | 5 | 0 | 0% |
| partial_delivery | 10 | 0 | 10 | 0 | 0% |
| refund | 10 | 0 | 10 | 0 | 0% |
| return | 10 | 0 | 10 | 0 | 0% |

## Exception Report

### bench_conflicting_evidence_000 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** clear
- **Expected amount:** ₹73,600
- **Actual amount:** ₹73,600
- **Reason:** Classification mismatch: expected ambiguous, got clear
- **Decision ID:** dec_1a9f81fe
- **Human review:** No

### bench_conflicting_evidence_001 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** clear
- **Expected amount:** ₹23,000
- **Actual amount:** ₹23,000
- **Reason:** Classification mismatch: expected ambiguous, got clear
- **Decision ID:** dec_0bb6475e
- **Human review:** No

### bench_conflicting_evidence_002 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** clear
- **Expected amount:** ₹69,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected ambiguous, got clear
- **Decision ID:** dec_e91633cf
- **Human review:** No

### bench_conflicting_evidence_003 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** clear
- **Expected amount:** ₹110,400
- **Actual amount:** ₹110,400
- **Reason:** Classification mismatch: expected ambiguous, got clear
- **Decision ID:** dec_5c624fe6
- **Human review:** No

### bench_conflicting_evidence_004 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** clear
- **Expected amount:** ₹46,000
- **Actual amount:** ₹46,000
- **Reason:** Classification mismatch: expected ambiguous, got clear
- **Decision ID:** dec_cbf17298
- **Human review:** No

### bench_fee_mismatch_000 (fee_mismatch)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹98,400
- **Actual amount:** ₹98,400
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_a3d78a94
- **Human review:** No

### bench_fee_mismatch_001 (fee_mismatch)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹11,000
- **Actual amount:** ₹11,000
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_5bcbeaa9
- **Human review:** No

### bench_fee_mismatch_002 (fee_mismatch)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹61,600
- **Actual amount:** ₹61,600
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_46c223d9
- **Human review:** No

### bench_fee_mismatch_003 (fee_mismatch)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹57,000
- **Actual amount:** ₹57,000
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_b678b5ea
- **Human review:** No

### bench_fee_mismatch_004 (fee_mismatch)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹11,000
- **Actual amount:** ₹11,000
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_56e7b507
- **Human review:** No

### bench_missing_delivery_000 (missing_delivery)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹73,600
- **Actual amount:** ₹73,600
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_516bfd45
- **Human review:** No

### bench_missing_delivery_001 (missing_delivery)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹184,000
- **Actual amount:** ₹184,000
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_6a073505
- **Human review:** No

### bench_missing_delivery_002 (missing_delivery)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹73,600
- **Actual amount:** ₹73,600
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_b08f345c
- **Human review:** No

### bench_missing_delivery_003 (missing_delivery)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹36,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_0b31816b
- **Human review:** No

### bench_missing_delivery_004 (missing_delivery)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹92,000
- **Actual amount:** ₹92,000
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_cda1b55c
- **Human review:** No

## Limitations

- Synthetic benchmark ≠ production guarantee
- Benchmark accuracy ≠ financial correctness without controls
- Unresolved cases are intentionally escalated for human review
- Deterministic calculation is authoritative; AI handles evidence interpretation only
- Mock mode tests pipeline correctness, not LLM reasoning quality

## Reproducibility

- **Dataset seed:** 42
- **Dataset version:** 1.0
- **Benchmark version:** 1.0
- **Model:** mock
- **Provider:** mock
- **Timestamp:** 2026-08-31T11:37:42Z