# Finance Controller Mock / Regression Benchmark

## Dataset

- **Cases:** 100
- **Seed:** 42
- **Provider:** mock
- **Model:** mock
- **Duration:** 0.3s
- **Run ID:** bench_859eace3dc00
- **Timestamp:** 2026-08-31T12:10:32Z

## Classification

| Metric | Value |
|--------|-------|
| Correct classification | 85/100 |
| Incorrect classification | 15/100 |
| **Classification accuracy** | **85.0%** |

## Decision Status

| Metric | Value |
|--------|-------|
| Correctly approved | 0/100 |
| Correctly review-required | 100/100 |
| Correctly exception | 0/100 |
| **False auto-resolution** | **0/100** |
| False auto-resolution rate | 0.0% |

## Financial

| Metric | Value |
|--------|-------|
| Exact calculation matches | 90/100 |
| **Calculation accuracy** | **90.0%** |

## Integrity

| Metric | Value |
|--------|-------|
| Audit completeness | 100.0% |
| Duplicate decisions | 0 |

## Performance

| Metric | Value |
|--------|-------|
| Throughput | 377.4 cases/sec |
| Avg latency | 9 ms |
| P50 latency | 1 ms |
| P95 latency | 2 ms |
| P99 latency | 209 ms |

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
- **Decision ID:** dec_3c880749
- **Human review:** No

### bench_conflicting_evidence_001 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** clear
- **Expected amount:** ₹23,000
- **Actual amount:** ₹23,000
- **Reason:** Classification mismatch: expected ambiguous, got clear
- **Decision ID:** dec_637b7269
- **Human review:** No

### bench_conflicting_evidence_002 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** clear
- **Expected amount:** ₹69,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected ambiguous, got clear
- **Decision ID:** dec_6a32538e
- **Human review:** No

### bench_conflicting_evidence_003 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** clear
- **Expected amount:** ₹110,400
- **Actual amount:** ₹110,400
- **Reason:** Classification mismatch: expected ambiguous, got clear
- **Decision ID:** dec_b635cda5
- **Human review:** No

### bench_conflicting_evidence_004 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** clear
- **Expected amount:** ₹46,000
- **Actual amount:** ₹46,000
- **Reason:** Classification mismatch: expected ambiguous, got clear
- **Decision ID:** dec_3732bc64
- **Human review:** No

### bench_fee_mismatch_000 (fee_mismatch)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹98,400
- **Actual amount:** ₹98,400
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_0779a305
- **Human review:** No

### bench_fee_mismatch_001 (fee_mismatch)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹11,000
- **Actual amount:** ₹11,000
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_2eada38c
- **Human review:** No

### bench_fee_mismatch_002 (fee_mismatch)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹61,600
- **Actual amount:** ₹61,600
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_31120bf3
- **Human review:** No

### bench_fee_mismatch_003 (fee_mismatch)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹57,000
- **Actual amount:** ₹57,000
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_091edf02
- **Human review:** No

### bench_fee_mismatch_004 (fee_mismatch)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹11,000
- **Actual amount:** ₹11,000
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_66c7ae6d
- **Human review:** No

### bench_missing_delivery_000 (missing_delivery)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹73,600
- **Actual amount:** ₹73,600
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_234564cd
- **Human review:** No

### bench_missing_delivery_001 (missing_delivery)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹184,000
- **Actual amount:** ₹184,000
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_2156e23a
- **Human review:** No

### bench_missing_delivery_002 (missing_delivery)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹73,600
- **Actual amount:** ₹73,600
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_9442f0e4
- **Human review:** No

### bench_missing_delivery_003 (missing_delivery)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹36,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_504a3a76
- **Human review:** No

### bench_missing_delivery_004 (missing_delivery)
- **Expected:** exception
- **Actual:** clear
- **Expected amount:** ₹92,000
- **Actual amount:** ₹92,000
- **Reason:** Classification mismatch: expected exception, got clear
- **Decision ID:** dec_7b0b405a
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
- **Timestamp:** 2026-08-31T12:10:32Z