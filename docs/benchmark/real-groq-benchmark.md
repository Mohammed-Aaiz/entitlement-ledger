# Finance Controller Real AI Benchmark

## Dataset

- **Cases:** 100
- **Seed:** 42
- **Provider:** groq
- **Model:** openai/gpt-oss-120b
- **Duration:** 111.8s
- **Run ID:** bench_0a0c3cdfea17
- **Timestamp:** 2026-08-31T12:29:41Z

## Classification

| Metric | Value |
|--------|-------|
| Correct classification | 8/100 |
| Incorrect classification | 92/100 |
| **Classification accuracy** | **8.0%** |

## Decision Status

| Metric | Value |
|--------|-------|
| Correctly approved | 0/100 |
| Correctly review-required | 92/100 |
| Correctly exception | 8/100 |
| **False auto-resolution** | **0/100** |
| False auto-resolution rate | 0.0% |

## Financial

| Metric | Value |
|--------|-------|
| Exact calculation matches | 49/92 |
| **Calculation accuracy** | **53.3%** |

## Integrity

| Metric | Value |
|--------|-------|
| Audit completeness | 92.0% |
| Duplicate decisions | 0 |

## Performance

| Metric | Value |
|--------|-------|
| Throughput | 0.9 cases/sec |
| Avg latency | 1118 ms |
| P50 latency | 755 ms |
| P95 latency | 2622 ms |
| P99 latency | 12420 ms |

## Agent Metrics

| Metric | Value |
|--------|-------|
| Avg tool calls/case | 0.0 |
| Avg iterations/case | 1.0 |
| Requiring tools | 1.0% |
| Resolving without tools | 99.0% |
| Agent success rate | 0.0% |
| Max tool calls | 1 |
| Max iterations | 2 |

## Category Breakdown

| Category | Total | Correct | Review | Exceptions | Match Rate |
|----------|-------|---------|--------|------------|------------|
| clean_payment | 20 | 0 | 18 | 2 | 0% |
| conflicting_evidence | 5 | 0 | 5 | 0 | 0% |
| delivered_on_time | 15 | 0 | 13 | 2 | 0% |
| duplicate_event | 5 | 0 | 5 | 0 | 0% |
| fee_mismatch | 5 | 0 | 4 | 1 | 0% |
| late_delivery | 15 | 0 | 14 | 1 | 0% |
| missing_delivery | 5 | 0 | 4 | 1 | 0% |
| partial_delivery | 10 | 0 | 9 | 1 | 0% |
| refund | 10 | 0 | 10 | 0 | 0% |
| return | 10 | 0 | 10 | 0 | 0% |

## Exception Report

### bench_clean_payment_000 (clean_payment)
- **Expected:** clear
- **Actual:** sufficient_evidence
- **Expected amount:** ₹36,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got sufficient_evidence
- **Decision ID:** dec_e996eda5
- **Human review:** No

### bench_clean_payment_001 (clean_payment)
- **Expected:** clear
- **Actual:** fee_applicable
- **Expected amount:** ₹69,000
- **Actual amount:** ₹0
- **Reason:** ValueError: Invalid references: Claim references non-existent policy: platform_1_1_fee
- **Decision ID:** None
- **Human review:** Yes

### bench_clean_payment_002 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹110,400
- **Actual amount:** ₹110,400
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_a72d9768
- **Human review:** Yes

### bench_clean_payment_003 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹36,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_de88106f
- **Human review:** Yes

### bench_clean_payment_004 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹184,000
- **Actual amount:** ₹184,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_554d1d2b
- **Human review:** Yes

### bench_clean_payment_005 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹184,000
- **Actual amount:** ₹184,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_91fe52f8
- **Human review:** Yes

### bench_clean_payment_006 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹69,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_df96d779
- **Human review:** Yes

### bench_clean_payment_007 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹46,000
- **Actual amount:** ₹46,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_59d34c79
- **Human review:** Yes

### bench_clean_payment_008 (clean_payment)
- **Expected:** clear
- **Actual:** fee_applicable
- **Expected amount:** ₹92,000
- **Actual amount:** ₹0
- **Reason:** ValueError: Invalid references: Claim references non-existent policy: platform_1_1_fee
- **Decision ID:** None
- **Human review:** Yes

### bench_clean_payment_009 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹69,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_b13c4599
- **Human review:** Yes

### bench_clean_payment_010 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹36,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_00102e8d
- **Human review:** Yes

### bench_clean_payment_011 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹92,000
- **Actual amount:** ₹92,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_624e7286
- **Human review:** Yes

### bench_clean_payment_012 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹73,600
- **Actual amount:** ₹73,600
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_53f82501
- **Human review:** Yes

### bench_clean_payment_013 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹138,000
- **Actual amount:** ₹138,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_12936ba3
- **Human review:** Yes

### bench_clean_payment_014 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹110,400
- **Actual amount:** ₹110,400
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_fe02a865
- **Human review:** Yes

### bench_clean_payment_015 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹69,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_7007afb1
- **Human review:** Yes

### bench_clean_payment_016 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹36,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_d959593c
- **Human review:** Yes

### bench_clean_payment_017 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹36,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_6eccaf68
- **Human review:** Yes

### bench_clean_payment_018 (clean_payment)
- **Expected:** clear
- **Actual:** compliant
- **Expected amount:** ₹138,000
- **Actual amount:** ₹138,000
- **Reason:** Classification mismatch: expected clear, got compliant
- **Decision ID:** dec_959e4ab0
- **Human review:** No

### bench_clean_payment_019 (clean_payment)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹92,000
- **Actual amount:** ₹92,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_6f51bbf4
- **Human review:** Yes

### bench_conflicting_evidence_000 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** exception
- **Expected amount:** ₹73,600
- **Actual amount:** ₹73,600
- **Reason:** Classification mismatch: expected ambiguous, got exception
- **Decision ID:** dec_b7e25fd0
- **Human review:** Yes

### bench_conflicting_evidence_001 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** exception
- **Expected amount:** ₹23,000
- **Actual amount:** ₹23,000
- **Reason:** Classification mismatch: expected ambiguous, got exception
- **Decision ID:** dec_48f3c432
- **Human review:** Yes

### bench_conflicting_evidence_002 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** exception
- **Expected amount:** ₹69,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected ambiguous, got exception
- **Decision ID:** dec_41677b20
- **Human review:** Yes

### bench_conflicting_evidence_003 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** exception
- **Expected amount:** ₹110,400
- **Actual amount:** ₹110,400
- **Reason:** Classification mismatch: expected ambiguous, got exception
- **Decision ID:** dec_d3ce6e8c
- **Human review:** Yes

### bench_conflicting_evidence_004 (conflicting_evidence)
- **Expected:** ambiguous
- **Actual:** exception
- **Expected amount:** ₹46,000
- **Actual amount:** ₹46,000
- **Reason:** Classification mismatch: expected ambiguous, got exception
- **Decision ID:** dec_3709ce7d
- **Human review:** Yes

### bench_delivered_on_time_000 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹184,000
- **Actual amount:** ₹184,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_8437058e
- **Human review:** Yes

### bench_delivered_on_time_001 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹36,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_967bda55
- **Human review:** Yes

### bench_delivered_on_time_002 (delivered_on_time)
- **Expected:** clear
- **Actual:** eligible
- **Expected amount:** ₹23,000
- **Actual amount:** ₹0
- **Reason:** ValueError: Invalid references: Claim references non-existent policy: platform_1_1_fee; Claim references non-existent policy: platform_1_1_on_time
- **Decision ID:** None
- **Human review:** Yes

### bench_delivered_on_time_003 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹46,000
- **Actual amount:** ₹46,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_77c65b36
- **Human review:** Yes

### bench_delivered_on_time_004 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹138,000
- **Actual amount:** ₹138,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_05cf41df
- **Human review:** Yes

### bench_delivered_on_time_005 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹73,600
- **Actual amount:** ₹73,600
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_88408c99
- **Human review:** Yes

### bench_delivered_on_time_006 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹138,000
- **Actual amount:** ₹138,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_7839da5d
- **Human review:** Yes

### bench_delivered_on_time_007 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹138,000
- **Actual amount:** ₹138,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_173e5af6
- **Human review:** Yes

### bench_delivered_on_time_008 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹69,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_33257e44
- **Human review:** Yes

### bench_delivered_on_time_009 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹110,400
- **Actual amount:** ₹110,400
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_b14dbafc
- **Human review:** Yes

### bench_delivered_on_time_010 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹36,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_baae8102
- **Human review:** Yes

### bench_delivered_on_time_011 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹23,000
- **Actual amount:** ₹23,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_2303d28f
- **Human review:** Yes

### bench_delivered_on_time_012 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹69,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_ddeaa029
- **Human review:** Yes

### bench_delivered_on_time_013 (delivered_on_time)
- **Expected:** clear
- **Actual:** compliant
- **Expected amount:** ₹73,600
- **Actual amount:** ₹0
- **Reason:** ValueError: Invalid references: Claim references non-existent policy: platform_1_1_fee; Claim references non-existent policy: platform_1_1_delivery_on_time
- **Decision ID:** None
- **Human review:** Yes

### bench_delivered_on_time_014 (delivered_on_time)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹138,000
- **Actual amount:** ₹138,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_0e4f5ad5
- **Human review:** Yes

### bench_duplicate_event_000 (duplicate_event)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹24,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_94ba590f
- **Human review:** Yes

### bench_duplicate_event_001 (duplicate_event)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹11,000
- **Actual amount:** ₹23,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_d9f50611
- **Human review:** Yes

### bench_duplicate_event_002 (duplicate_event)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹24,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_346d0bd9
- **Human review:** Yes

### bench_duplicate_event_003 (duplicate_event)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹34,000
- **Actual amount:** ₹46,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_64006e22
- **Human review:** Yes

### bench_duplicate_event_004 (duplicate_event)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹24,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_8e29db6f
- **Human review:** Yes

### bench_fee_mismatch_002 (fee_mismatch)
- **Expected:** exception
- **Actual:** fee_mismatch_evaluation
- **Expected amount:** ₹61,600
- **Actual amount:** ₹0
- **Reason:** ValueError: Invalid references: Claim references non-existent policy: platform_1_1_fee; Claim references non-existent policy: sla_4_2_penalty
- **Decision ID:** None
- **Human review:** Yes

### bench_late_delivery_000 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹98,400
- **Actual amount:** ₹110,400
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_cc48bb33
- **Human review:** Yes

### bench_late_delivery_001 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹61,600
- **Actual amount:** ₹73,600
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_e5cc35b1
- **Human review:** Yes

### bench_late_delivery_002 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹61,600
- **Actual amount:** ₹73,600
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_7ffda84b
- **Human review:** Yes

### bench_late_delivery_003 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹172,000
- **Actual amount:** ₹184,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_51c93681
- **Human review:** Yes

### bench_late_delivery_004 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹57,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_9eda81d9
- **Human review:** Yes

### bench_late_delivery_005 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹34,000
- **Actual amount:** ₹46,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_0688b247
- **Human review:** Yes

### bench_late_delivery_006 (late_delivery)
- **Expected:** clear
- **Actual:** deduction_applicable
- **Expected amount:** ₹61,600
- **Actual amount:** ₹0
- **Reason:** ValueError: Invalid references: Claim references non-existent policy: platform_1_1_fee; Claim references non-existent policy: sla_4_2_penalty
- **Decision ID:** None
- **Human review:** Yes

### bench_late_delivery_007 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹11,000
- **Actual amount:** ₹23,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_6f3b99fe
- **Human review:** Yes

### bench_late_delivery_008 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹172,000
- **Actual amount:** ₹184,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_8feab7f8
- **Human review:** Yes

### bench_late_delivery_009 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹80,000
- **Actual amount:** ₹92,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_029542b1
- **Human review:** Yes

### bench_late_delivery_010 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹11,000
- **Actual amount:** ₹23,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_937afced
- **Human review:** Yes

### bench_late_delivery_011 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹34,000
- **Actual amount:** ₹46,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_8245c566
- **Human review:** Yes

### bench_late_delivery_012 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹80,000
- **Actual amount:** ₹92,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_75d0b5c8
- **Human review:** Yes

### bench_late_delivery_013 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹11,000
- **Actual amount:** ₹23,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_a6369495
- **Human review:** Yes

### bench_late_delivery_014 (late_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹57,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_c696f19c
- **Human review:** Yes

### bench_missing_delivery_000 (missing_delivery)
- **Expected:** exception
- **Actual:** fee_applicable
- **Expected amount:** ₹73,600
- **Actual amount:** ₹0
- **Reason:** ValueError: Invalid references: Claim references non-existent policy: platform_1_1_fee
- **Decision ID:** None
- **Human review:** Yes

### bench_partial_delivery_000 (partial_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹184,000
- **Actual amount:** ₹184,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_755ee39a
- **Human review:** Yes

### bench_partial_delivery_001 (partial_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹110,400
- **Actual amount:** ₹110,400
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_71b68f08
- **Human review:** Yes

### bench_partial_delivery_002 (partial_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹92,000
- **Actual amount:** ₹92,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_206d4c58
- **Human review:** Yes

### bench_partial_delivery_003 (partial_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹110,400
- **Actual amount:** ₹110,400
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_21f8acaf
- **Human review:** Yes

### bench_partial_delivery_004 (partial_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹46,000
- **Actual amount:** ₹46,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_16772b05
- **Human review:** Yes

### bench_partial_delivery_005 (partial_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹69,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_55f0a714
- **Human review:** Yes

### bench_partial_delivery_006 (partial_delivery)
- **Expected:** clear
- **Actual:** insufficient_evidence
- **Expected amount:** ₹69,000
- **Actual amount:** ₹0
- **Reason:** ValueError: Invalid references: Claim references non-existent policy: platform_1_1_fee
- **Decision ID:** None
- **Human review:** Yes

### bench_partial_delivery_007 (partial_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹73,600
- **Actual amount:** ₹73,600
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_bfeacfe4
- **Human review:** Yes

### bench_partial_delivery_008 (partial_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹46,000
- **Actual amount:** ₹46,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_d3b1ff18
- **Human review:** Yes

### bench_partial_delivery_009 (partial_delivery)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹36,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_991962f2
- **Human review:** Yes

### bench_refund_000 (refund)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹57,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_d41fc94f
- **Human review:** Yes

### bench_refund_001 (refund)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹126,000
- **Actual amount:** ₹138,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_f459abd6
- **Human review:** Yes

### bench_refund_002 (refund)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹126,000
- **Actual amount:** ₹138,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_f25b411d
- **Human review:** Yes

### bench_refund_003 (refund)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹24,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_9daa91a5
- **Human review:** Yes

### bench_refund_004 (refund)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹98,400
- **Actual amount:** ₹110,400
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_43573fd7
- **Human review:** Yes

### bench_refund_005 (refund)
- **Expected:** clear
- **Actual:** financial_evaluation
- **Expected amount:** ₹126,000
- **Actual amount:** ₹138,000
- **Reason:** Classification mismatch: expected clear, got financial_evaluation
- **Decision ID:** dec_d95898e2
- **Human review:** No

### bench_refund_006 (refund)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹126,000
- **Actual amount:** ₹138,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_0ed44a55
- **Human review:** Yes

### bench_refund_007 (refund)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹172,000
- **Actual amount:** ₹184,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_c825a298
- **Human review:** Yes

### bench_refund_008 (refund)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹61,600
- **Actual amount:** ₹73,600
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_0fa47fcf
- **Human review:** Yes

### bench_refund_009 (refund)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹126,000
- **Actual amount:** ₹138,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_9d5cbdac
- **Human review:** Yes

### bench_return_000 (return)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹52,000
- **Actual amount:** ₹69,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_dcea629e
- **Human review:** Yes

### bench_return_001 (return)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹29,000
- **Actual amount:** ₹46,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_80ec1c87
- **Human review:** Yes

### bench_return_002 (return)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹19,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_f8d740c7
- **Human review:** Yes

### bench_return_003 (return)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹93,400
- **Actual amount:** ₹110,400
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_002b1405
- **Human review:** Yes

### bench_return_004 (return)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹93,400
- **Actual amount:** ₹110,400
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_7043816c
- **Human review:** Yes

### bench_return_005 (return)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹19,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_14528011
- **Human review:** Yes

### bench_return_006 (return)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹29,000
- **Actual amount:** ₹46,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_df5a6056
- **Human review:** Yes

### bench_return_007 (return)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹19,800
- **Actual amount:** ₹36,800
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_e03354bb
- **Human review:** Yes

### bench_return_008 (return)
- **Expected:** clear
- **Actual:** deductions_identified
- **Expected amount:** ₹6,000
- **Actual amount:** ₹23,000
- **Reason:** Classification mismatch: expected clear, got deductions_identified
- **Decision ID:** dec_89ba8206
- **Human review:** No

### bench_return_009 (return)
- **Expected:** clear
- **Actual:** exception
- **Expected amount:** ₹167,000
- **Actual amount:** ₹184,000
- **Reason:** Classification mismatch: expected clear, got exception
- **Decision ID:** dec_5748a3e7
- **Human review:** Yes

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
- **Model:** openai/gpt-oss-120b
- **Provider:** groq
- **Timestamp:** 2026-08-31T12:29:41Z