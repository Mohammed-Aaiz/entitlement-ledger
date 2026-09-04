# Test Suite Consolidation Audit

**Date:** 2026-09-01
**Total tests:** 597 (16 files)
**Status:** Audit only — no files deleted, no behavior changed

---

## 1. INVENTORY

### Core Production Tests (KEEP)

| File | Tests | Purpose | Type | Verdict |
|------|-------|---------|------|---------|
| `test_agent.py` | 62 | Agent core: tool calling, boundaries, mock tools, end-to-end, native tool calling, idempotency | UNIT | **KEEP** — core agent coverage |
| `test_ai_pipeline.py` | 120 | AI pipeline validation, extraction/reasoning schemas, reference validation, contradiction detection, provider unit tests (OpenRouter, Gemini, Groq, Anthropic), schema conversion | UNIT | **KEEP** — core pipeline + provider unit coverage |
| `test_analyze.py` | 15 | /api/scenarios/{id}/run endpoint integration | INTEGRATION | **KEEP** — endpoint integration |
| `test_auth.py` | 21 | Registration, login, protected endpoints, tenant isolation | UNIT/SECURITY | **KEEP** — auth and security |
| `test_calculations.py` | 22 | Deterministic calculation engine + hash chain | UNIT | **KEEP** — calculation authority |
| `test_rate_limit.py` | 9 | HTTP rate limiting middleware | UNIT | **KEEP** — HTTP middleware |
| `test_razorpay.py` | 34 | Webhook security, tenant resolution, simulator, process event, seeds visible | INTEGRATION/SECURITY | **KEEP** — Razorpay webhook security |
| `test_razorpay_integration.py` | 17 | Fact extraction, evidence creation, decision creation, end-to-end | INTEGRATION | **KEEP** — Razorpay integration |
| `test_production_architecture.py` | 83 | Production seed isolation, system config, real evidence, deterministic calc, run scenario, AI analyzed, idempotency flow, analysis fingerprint, tenant isolation, calculation trace, exception model, decision replay, hash verification | INTEGRATION | **KEEP** — production architecture safety |

### Phase Tests (CONSOLIDATE)

| File | Tests | Purpose | Type | Verdict |
|------|-------|---------|------|---------|
| `test_phase6.py` | 47 | Gemini routing, failure taxonomy, claim mapping, sufficiency, compact context, provider parity, reference validation | UNIT | **CONSOLIDATE** — many duplicates |
| `test_phase7_real.py` | 28 | Real Groq execution, audit trail, failure taxonomy, claim mapping, sufficiency, compact context | REAL_PROVIDER | **CONSOLIDATE** — overlaps with phase6 + phase7_3 |
| `test_phase7_1.py` | 29 | Success state, approval gate, exception semantics, financial safety, audit completeness | UNIT | **CONSOLIDATE** — overlaps with phase7_3 |
| `test_phase7_2.py` | 37 | Policy outcome evaluation, Gemini unavailable safety, approval gate, comprehensive safety, rate limit | UNIT | **CONSOLIDATE** — overlaps with phase7_3 |
| `test_phase7_3_real.py` | 21 | Real Groq simple/complex, Gemini routing, Gemini safety, rate limit, success state, policy gate | REAL_PROVIDER | **CONSOLIDATE** — superset of phase7_1 + phase7_2 |
| `test_provider_phase4_2.py` | 25 | Ollama provider, provider parity, intelligent routing, safety, rate limit, Vercel safety | UNIT | **CONSOLIDATE** — overlaps with phase6 + ai_pipeline |
| `test_benchmark.py` | 43 | Dataset generation, reproducibility, ground truth, category cases, calculation mismatch, agent failures, idempotency, safety | BENCHMARK | **KEEP** — unique benchmark framework |

### Real-Provider Tests (SEPARATE)

| File | Tests | Purpose | Type |
|------|-------|---------|------|
| `test_phase7_real.py` | 28 | Real Groq execution (requires API key + rate limits) | REAL_PROVIDER |
| `test_phase7_3_real.py` | 21 | Real Groq simple/complex execution | REAL_PROVIDER |

---

## 2. DUPLICATION FINDINGS

### 2a. Approval Gate — HEAVILY DUPLICATED

The deterministic approval gate is tested in **4 files** with **27 tests**:

| File | Class | Tests | Unique? |
|------|-------|-------|---------|
| `test_phase7_1.py` | TestApprovalGate | 9 | Yes (financial safety cases) |
| `test_phase7_2.py` | TestPolicyOutcomeInApprovalGate | 4 | Partial (policy + gate) |
| `test_phase7_3_real.py` | TestPolicyOutcomeUnit | 14 | **Duplicate** of phase7_1 + phase7_2 |
| `test_provider_phase4_2.py` | TestSafetyGuarantees | 1 | **Duplicate** (provider failure → not approved) |

**Recommended:** Keep `test_phase7_1.py::TestApprovalGate` + `test_phase7_2.py::TestPolicyOutcomeInApprovalGate`. Remove `test_phase7_3_real.py::TestPolicyOutcomeUnit` (14 tests) and `test_provider_phase4_2.py::TestSafetyGuarantees::test_provider_failure_does_not_cause_approval` (1 test). Net savings: **15 tests**.

### 2b. Gemini Routing — DUPLICATED

Gemini routing is tested in **5 files** with **21 tests**:

| File | Class | Tests | Unique? |
|------|-------|-------|---------|
| `test_phase6.py` | TestGeminiRouting | 9 | **Canonical** — exhaustive routing cases |
| `test_phase7_real.py` | TestRealGeminiRouting | 6 | **Subset** of phase6 + real fallback test |
| `test_phase7_3_real.py` | TestRealSimpleCase, TestRealComplexCase | 2 | **Subset** of phase6 |
| `test_provider_phase4_2.py` | TestIntelligentRouting | 4 | Partial overlap |
| `test_phase6.py` | TestNoGeminiSimpleCase | 1 | Unique (pipeline level) |

**Recommended:** Keep `test_phase6.py::TestGeminiRouting` (canonical). Remove `test_phase7_real.py::TestRealGeminiRouting` (5 tests, keep only `test_gemini_fallback_when_unavailable`). Remove `test_phase7_3_real.py` routing tests (2 tests). Net savings: **7 tests**.

### 2c. Failure Taxonomy — DUPLICATED

Failure taxonomy is tested in **4 files** with **14 tests**:

| File | Class | Tests | Unique? |
|------|-------|-------|---------|
| `test_phase6.py` | TestFailureTaxonomy | 7 | **Canonical** — all failure types, classify functions |
| `test_phase7_real.py` | TestFailureTaxonomy | 4 | **Subset** of phase6 |
| `test_phase7_3_real.py` | TestRateLimitSemantics | 1 | **Duplicate** of phase6 |
| `test_phase7_2.py` | TestRateLimitClassification | 2 | **Duplicate** of phase6 |
| `test_provider_phase4_2.py` | TestRateLimitHandling | 4 | **Subset** + unique (bounded retry, no infinite) |

**Recommended:** Keep `test_phase6.py::TestFailureTaxonomy` + `test_provider_phase4_2.py::TestRateLimitHandling::test_bounded_retry` + `test_provider_phase4_2.py::TestRateLimitHandling::test_no_infinite_retry`. Remove the rest. Net savings: **8 tests**.

### 2d. Evidence Sufficiency — DUPLICATED

Evidence sufficiency is tested in **3 files** with **12 tests**:

| File | Class | Tests | Unique? |
|------|-------|-------|---------|
| `test_phase6.py` | TestEvidenceSufficiency | 5 | **Canonical** — all states |
| `test_phase7_real.py` | TestEvidenceSufficiency | 6 | **Subset** of phase6 |
| `test_phase7_1.py` | TestFinancialSafety | 2 | Partial overlap |

**Recommended:** Keep `test_phase6.py::TestEvidenceSufficiency` (canonical). Remove `test_phase7_real.py::TestEvidenceSufficiency` (5 tests). Net savings: **5 tests**.

### 2e. Claim Mapping — DUPLICATED

Claim mapping is tested in **2 files** with **8 tests**:

| File | Class | Tests | Unique? |
|------|-------|-------|---------|
| `test_phase6.py` | TestClaimMapping | 7 | **Canonical** — all claim types |
| `test_phase7_real.py` | TestClaimMapping | 3 | **Subset** of phase6 |

**Recommended:** Keep `test_phase6.py::TestClaimMapping` (canonical). Remove `test_phase7_real.py::TestClaimMapping` (2 tests). Net savings: **2 tests**.

### 2f. Policy Outcome Evaluation — DUPLICATED

Policy outcomes are tested in **3 files** with **14 tests**:

| File | Class | Tests | Unique? |
|------|-------|-------|---------|
| `test_phase7_2.py` | TestPolicyOutcomeEvaluation | 10 | **Canonical** — all claim types × outcomes |
| `test_phase7_3_real.py` | TestPolicyOutcomeUnit | 14 | **Superset** — covers phase7_2 + more gate paths |
| `test_phase7_2.py` | TestPolicyOutcomeInApprovalGate | 4 | Partial (gate integration) |

**Recommended:** Keep `test_phase7_2.py::TestPolicyOutcomeEvaluation` (canonical evaluation) + `test_phase7_2.py::TestPolicyOutcomeInApprovalGate` (gate integration). Remove `test_phase7_3_real.py::TestPolicyOutcomeUnit` (14 tests). Net savings: **14 tests**.

### 2g. Gemini Unavailable Safety — DUPLICATED

Gemini safety is tested in **3 files** with **6 tests**:

| File | Class | Tests | Unique? |
|------|-------|-------|---------|
| `test_phase7_2.py` | TestGeminiUnavailableSafety | 4 | **Canonical** — all 3 paths |
| `test_phase7_3_real.py` | TestGeminiUnavailableSafety | 1 | **Duplicate** of phase7_2 |
| `test_phase7_1.py` | TestFinancialSafety::test_case_f_provider_failure_never_approved | 1 | **Subset** |

**Recommended:** Keep `test_phase7_2.py::TestGeminiUnavailableSafety` (canonical). Remove `test_phase7_3_real.py::TestGeminiUnavailableSafety` (1 test). Net savings: **1 test**.

### 2h. Success State — DUPLICATED

Success state is tested in **3 files** with **7 tests**:

| File | Class | Tests | Unique? |
|------|-------|-------|---------|
| `test_phase7_1.py` | TestSuccessStateInvariant | 5 | **Canonical** — all state transitions |
| `test_phase7_3_real.py` | TestSuccessStateInvariant | 1 | **Subset** of phase7_1 |
| `test_phase7_2.py` | (preserved from phase7_1) | 0 | N/A |

**Recommended:** Keep `test_phase7_1.py::TestSuccessStateInvariant` (canonical). Remove `test_phase7_3_real.py::TestSuccessStateInvariant` (1 test). Net savings: **1 test**.

### 2i. Idempotency — DUPLICATED

Idempotency is tested in **5 files** with **9 tests**:

| File | Class | Tests | Unique? |
|------|-------|-------|---------|
| `test_agent.py` | TestNoDuplicateDecisionOnRetry | 3 | **Canonical** — agent-level |
| `test_production_architecture.py` | TestIdempotencyFlow | 5+ | **Canonical** — production flow |
| `test_phase7_real.py` | TestIdempotency | 3 | **Subset** of production_architecture |
| `test_benchmark.py` | TestBenchmarkIdempotency | 1 | Unique (benchmark-specific) |
| `test_razorpay_integration.py` | TestEndToEnd::test_duplicate_event_idempotent | 1 | Unique (Razorpay-specific) |

**Recommended:** Keep `test_agent.py::TestNoDuplicateDecisionOnRetry` + `test_production_architecture.py::TestIdempotencyFlow`. Remove `test_phase7_real.py::TestIdempotency` (2 tests). Net savings: **2 tests**.

### 2j. Audit/Hash — PARTIALLY DUPLICATED

Audit and hash are tested in **5 files** with **14 tests**:

| File | Class | Tests | Unique? |
|------|-------|-------|---------|
| `test_calculations.py` | TestHashChain | 7 | **Canonical** — hash mechanics |
| `test_phase7_real.py` | TestAuditTrail | 1 | **Overlap** with production_architecture |
| `test_phase7_1.py` | TestAuditCompleteness | 2 | **Overlap** with production_architecture |
| `test_production_architecture.py` | TestAuditTrail | 1 | **Canonical** — production audit |
| `test_production_architecture.py` | TestHashVerificationExtended | 1 | **Canonical** — extended hash verification |

**Recommended:** Keep `test_calculations.py::TestHashChain` + `test_production_architecture.py::TestAuditTrail` + `test_production_architecture.py::TestHashVerificationExtended`. Remove `test_phase7_real.py::TestAuditTrail` (1 test) and `test_phase7_1.py::TestAuditCompleteness` (2 tests). Net savings: **3 tests**.

### 2k. Calculation Authority — PARTIALLY DUPLICATED

Calculations are tested in **4 files** with **22 tests**:

| File | Class | Tests | Unique? |
|------|-------|-------|---------|
| `test_calculations.py` | TestFinancialCalculations | 11 | **Canonical** — calculation engine |
| `test_agent.py` | TestDeterministicCalculationAuthoritative | 3 | **Canonical** — agent does NOT override |
| `test_production_architecture.py` | TestDeterministicCalculations | 5 | **Subset** of calculations.py |
| `test_benchmark.py` | TestCalculationMismatch | 3 | **Unique** — LLM wording does not affect calc |

**Recommended:** Keep `test_calculations.py` + `test_agent.py::TestDeterministicCalculationAuthoritative` + `test_benchmark.py::TestCalculationMismatch`. Remove `test_production_architecture.py::TestDeterministicCalculations` (4 tests). Net savings: **4 tests**.

### 2l. Provider Parity — DUPLICATED

Provider parity is tested in **2 files** with **4 tests**:

| File | Class | Tests | Unique? |
|------|-------|-------|---------|
| `test_phase6.py` | TestProviderParity | 1 | **Canonical** — mock parity |
| `test_provider_phase4_2.py` | TestProviderParity | 3 | **Superset** — Ollama/Groq/Gemini parity |

**Recommended:** Keep `test_provider_phase4_2.py::TestProviderParity` (canonical). Remove `test_phase6.py::TestProviderParity` (1 test). Net savings: **1 test**.

### 2m. Vercel Safety — UNIQUE

Vercel production safety is tested in `test_provider_phase4_2.py::TestVercelSafety` (4 tests). **No duplicates. KEEP.**

### 2n. Prompt Injection — UNIQUE

Prompt injection detection is tested in `test_agent.py::TestPromptInjectionDetection` (3 tests). **No duplicates. KEEP.**

---

## 3. DUPLICATE TEST SUMMARY

| Behavior | Files | Total Tests | Canonical File | Duplicate Tests |
|----------|-------|-------------|----------------|-----------------|
| Approval gate | 4 | 27 | test_phase7_1.py + phase7_2.py | 15 |
| Gemini routing | 5 | 21 | test_phase6.py | 7 |
| Failure taxonomy | 4 | 14 | test_phase6.py | 8 |
| Evidence sufficiency | 3 | 12 | test_phase6.py | 5 |
| Claim mapping | 2 | 8 | test_phase6.py | 2 |
| Policy outcomes | 3 | 14 | test_phase7_2.py | 14 |
| Gemini safety | 3 | 6 | test_phase7_2.py | 1 |
| Success state | 3 | 7 | test_phase7_1.py | 1 |
| Idempotency | 5 | 9 | test_agent.py + production_arch | 2 |
| Audit/hash | 5 | 14 | calculations.py + production_arch | 3 |
| Calculation auth | 4 | 22 | calculations.py + agent.py | 4 |
| Provider parity | 2 | 4 | provider_phase4_2.py | 1 |
| **TOTAL** | | | | **63 tests** |

**After consolidation: 597 - 63 = 534 tests** (removing only exact duplicates)

---

## 4. COVERAGE MATRIX

| Behavior | test_agent.py | test_ai_pipeline.py | test_analyze.py | test_auth.py | test_benchmark.py | test_calculations.py | test_phase6.py | test_phase7_1.py | test_phase7_2.py | test_production_arch.py | test_provider_phase4_2.py | test_razorpay.py | test_razorpay_integration.py |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Native tool calling | **C** | | | | | | | | | | | | |
| Structured output | **C** | | | | | | | | | | | | |
| Groq controller | **C** | | | | | | | | | | | | |
| Gemini routing | | | | | | | **C** | | | | P | | |
| Gemini evidence intel | | | | | | | **C** | | | | | | |
| Gemini unavailable safety | | | | | | | | | **C** | | | | |
| Ollama provider | | | **C** | | | | | | | | **C** | | |
| Provider parity | | | | | | | P | | | | **C** | | |
| Evidence ref validation | | **C** | | | | | P | | | | | | |
| Policy ref validation | | **C** | | | | | P | | | | **C** | | |
| Policy outcome eval | | | | | | | | | **C** | | | | |
| Contradiction detection | | **C** | | | | | | | | | | | |
| Evidence sufficiency | | | | | | | **C** | | | | | | |
| Approval gate | | | | | | | | **C** | P | | | | |
| Provider failure | | | | | P | | **C** | | | | **C** | | |
| Rate limit | | | | | | | **C** | | P | | **C** | | |
| Timeout | | | | | | | **C** | | | | **C** | | |
| Agent limits | **C** | | | | P | | | | | | | | |
| Idempotency | **C** | | | | P | | | | | **C** | | | P |
| Audit/hash chain | | | | | | **C** | | | | **C** | | | |
| Calculation authority | | | **C** | | P | **C** | | | | P | | | |
| Tenant isolation | | | | **C** | | | | | | **C** | | | |
| Razorpay integration | | | | | | | | | | | | **C** | **C** |
| Webhook security | | | | | | | | | | | | **C** | |
| Success state | | | | | | | | **C** | | | | | |
| Claim mapping | | | | | | | **C** | | | | | | |
| Exception semantics | | | | | | | | **C** | | | | | |
| Financial safety | | | | | | | | **C** | **C** | | | | |
| Prompt injection | **C** | | | | | | | | | | | | |
| Compact context | | | | | | | **C** | | | | | | |

**C** = Canonical (primary coverage) | **P** = Partial/secondary coverage

---

## 5. RECOMMENDED FINAL STRUCTURE

### Flat structure (minimal change)

```
backend/
  test_agent.py                    # KEEP — agent core (62 tests)
  test_ai_pipeline.py              # KEEP — pipeline + providers (120 tests)
  test_analyze.py                  # KEEP — endpoint integration (15 tests)
  test_auth.py                     # KEEP — auth + security (21 tests)
  test_benchmark.py                # KEEP — benchmark framework (43 tests)
  test_calculations.py             # KEEP — calculation engine (22 tests)
  test_phase6.py                   # CONSOLIDATE — Gemini routing, failure taxonomy, claim mapping, sufficiency
  test_production_architecture.py  # KEEP — production safety (83 tests)
  test_provider_phase4_2.py        # CONSOLIDATE — Ollama, provider parity, Vercel safety
  test_rate_limit.py               # KEEP — HTTP middleware (9 tests)
  test_razorpay.py                 # KEEP — webhook security (34 tests)
  test_razorpay_integration.py     # KEEP — Razorpay integration (17 tests)
  test_real_providers.py           # NEW — consolidate phase7_real + phase7_3_real
  test_approval_gate.py            # NEW — consolidate phase7_1 + phase7_2 gate tests
```

### Why NOT a tests/ directory?

The project currently has all test files in `backend/` alongside production code. Moving them would:
- Require updating all imports
- Risk breaking test discovery patterns
- Add complexity for marginal organizational benefit

The current flat structure works. Consolidation should focus on removing duplicates, not reorganizing.

---

## 6. PHASE TEST ANALYSIS

### test_phase6.py (47 tests)

| Class | Tests | Status |
|-------|-------|--------|
| TestGeminiRouting | 9 | **KEEP** — canonical routing |
| TestFailureTaxonomy | 7 | **KEEP** — canonical taxonomy |
| TestClaimMapping | 7 | **KEEP** — canonical claim mapping |
| TestEvidenceSufficiency | 5 | **KEEP** — canonical sufficiency |
| TestCompactContext | 3 | **KEEP** — unique compact context |
| TestEvidenceIntelligence | 2 | **KEEP** — unique Gemini contract |
| TestProviderParity | 1 | **REMOVE** — duplicate of provider_phase4_2.py |
| TestPipelineReferenceValidation | 12 | **KEEP** — unique reference validation depth |
| TestNoGeminiSimpleCase | 1 | **KEEP** — unique pipeline-level |

**Verdict:** KEEP `test_phase6.py` almost entirely. Remove only `TestProviderParity` (1 test).

### test_phase7_real.py (28 tests)

| Class | Tests | Status |
|-------|-------|--------|
| TestRealGroqSimpleCase | 1 | **KEEP** — real Groq execution |
| TestRealGeminiRouting | 6 | **REMOVE** — 5/6 are subsets of phase6 |
| TestAuditTrail | 1 | **REMOVE** — subset of production_architecture |
| TestProviderFailureFailClosed | 1 | **REMOVE** — subset of phase6/provider_phase4_2 |
| TestInsufficientEvidenceFailClosed | 1 | **REMOVE** — subset of phase6 |
| TestIdempotency | 3 | **REMOVE** — subset of production_architecture |
| TestFailureTaxonomy | 4 | **REMOVE** — subset of phase6 |
| TestClaimMapping | 3 | **REMOVE** — subset of phase6 |
| TestEvidenceSufficiency | 6 | **REMOVE** — subset of phase6 |
| TestCompactContext | 2 | **REMOVE** — subset of phase6 |

**Verdict:** Remove 26 tests. Keep only `TestRealGroqSimpleCase` (1 test). Merge into `test_real_providers.py`.

### test_phase7_1.py (29 tests)

| Class | Tests | Status |
|-------|-------|--------|
| TestSuccessStateInvariant | 5 | **KEEP** — canonical success state |
| TestApprovalGate | 9 | **KEEP** — canonical approval gate |
| TestExceptionSemantics | 3 | **KEEP** — unique exception semantics |
| TestFinancialSafety | 9 | **MERGE** — keep cases A-H, remove G (idempotency overlap) |
| TestAuditCompleteness | 2 | **REMOVE** — subset of production_architecture |

**Verdict:** Remove 2 tests. Keep 27. Merge into `test_approval_gate.py`.

### test_phase7_2.py (37 tests)

| Class | Tests | Status |
|-------|-------|--------|
| TestPolicyOutcomeEvaluation | 10 | **KEEP** — canonical policy evaluation |
| TestGeminiUnavailableSafety | 4 | **KEEP** — canonical Gemini safety |
| TestPolicyOutcomeInApprovalGate | 4 | **KEEP** — unique gate integration |
| TestComprehensiveSafetyCases | 10 | **MERGE** — keep cases A-J |
| TestRateLimitClassification | 2 | **REMOVE** — subset of phase6 |
| TestAutoApprovalSafety | 7 | **KEEP** — unique auto-approval safety |

**Verdict:** Remove 2 tests. Keep 35. Merge into `test_approval_gate.py`.

### test_phase7_3_real.py (21 tests)

| Class | Tests | Status |
|-------|-------|--------|
| TestRealSimpleCase | 2 | **MERGE** into test_real_providers.py |
| TestRealComplexCase | 2 | **MERGE** into test_real_providers.py |
| TestGeminiUnavailableSafety | 1 | **REMOVE** — duplicate of phase7_2 |
| TestRateLimitSemantics | 1 | **REMOVE** — duplicate of phase6 |
| TestSuccessStateInvariant | 1 | **REMOVE** — subset of phase7_1 |
| TestPolicyOutcomeUnit | 14 | **REMOVE** — superset of phase7_1 + phase7_2 |

**Verdict:** Remove 17 tests. Keep 4. Merge into `test_real_providers.py`.

### test_provider_phase4_2.py (25 tests)

| Class | Tests | Status |
|-------|-------|--------|
| TestOllamaProvider | 4 | **KEEP** — Ollama-specific |
| TestProviderParity | 3 | **KEEP** — canonical parity |
| TestIntelligentRouting | 4 | **KEEP** — unique routing tests |
| TestSafetyGuarantees | 7 | **MERGE** — keep unique safety tests, remove duplicates |
| TestRateLimitHandling | 4 | **KEEP** — unique bounded retry tests |
| TestVercelSafety | 4 | **KEEP** — unique Vercel safety |

**Verdict:** Remove 1 duplicate (provider_failure_does_not_cause_approval). Keep 24.

---

## 7. REAL PROVIDER TEST POLICY

### Current state
- `test_phase7_real.py` (28 tests) — requires real Groq API, hits rate limits
- `test_phase7_3_real.py` (21 tests) — requires real Groq API, hits rate limits

### Recommended

Create `test_real_providers.py` consolidating:
- Real Groq simple case execution
- Real Groq complex case execution
- Gemini routing verification (deterministic, no API needed)
- Gemini fallback when unavailable

**Rules:**
1. Real-provider tests use `@pytest.mark.slow` decorator
2. Default CI runs `pytest -m "not slow"`
3. Real-provider tests include rate-limit retry with backoff
4. Tests verify fail-closed behavior when rate-limited
5. No benchmarks — those are in `test_benchmark.py`

---

## 8. RECOMMENDED CONSOLIDATION PLAN

### Step 1: Create `test_real_providers.py` (consolidate real-provider tests)

Merge from:
- `test_phase7_real.py::TestRealGroqSimpleCase` (1 test)
- `test_phase7_3_real.py::TestRealSimpleCase` (2 tests)
- `test_phase7_3_real.py::TestRealComplexCase` (2 tests)
- `test_phase7_real.py::TestRealGeminiRouting::test_gemini_fallback_when_unavailable` (1 test)

**Result:** 6 tests in 1 file (down from 49 tests in 2 files)

### Step 2: Create `test_approval_gate.py` (consolidate gate tests)

Merge from:
- `test_phase7_1.py::TestSuccessStateInvariant` (5 tests)
- `test_phase7_1.py::TestApprovalGate` (9 tests)
- `test_phase7_1.py::TestExceptionSemantics` (3 tests)
- `test_phase7_1.py::TestFinancialSafety` (9 tests)
- `test_phase7_2.py::TestPolicyOutcomeEvaluation` (10 tests)
- `test_phase7_2.py::TestGeminiUnavailableSafety` (4 tests)
- `test_phase7_2.py::TestPolicyOutcomeInApprovalGate` (4 tests)
- `test_phase7_2.py::TestComprehensiveSafetyCases` (10 tests)
- `test_phase7_2.py::TestAutoApprovalSafety` (7 tests)

**Result:** 61 tests in 1 file (down from 66 tests in 2 files, removing 5 duplicates)

### Step 3: Trim `test_phase6.py`

Remove:
- `TestProviderParity` (1 test — duplicate of provider_phase4_2.py)

**Result:** 46 tests (down from 47)

### Step 4: Trim `test_provider_phase4_2.py`

Remove:
- `TestSafetyGuarantees::test_provider_failure_does_not_cause_approval` (1 test — duplicate)

**Result:** 24 tests (down from 25)

### Step 5: Delete phase files (after merge)

- `test_phase7_real.py` → merged into `test_real_providers.py`
- `test_phase7_3_real.py` → merged into `test_real_providers.py`
- `test_phase7_1.py` → merged into `test_approval_gate.py`
- `test_phase7_2.py` → merged into `test_approval_gate.py`

### Step 6: Mark real-provider tests

Add `@pytest.mark.slow` to `test_real_providers.py` tests.

---

## 9. FINAL RETAINED FILES

| File | Tests | Type | Status |
|------|-------|------|--------|
| test_agent.py | 62 | UNIT | **KEEP** |
| test_ai_pipeline.py | 120 | UNIT | **KEEP** |
| test_analyze.py | 15 | INTEGRATION | **KEEP** |
| test_auth.py | 21 | UNIT/SECURITY | **KEEP** |
| test_benchmark.py | 43 | BENCHMARK | **KEEP** |
| test_calculations.py | 22 | UNIT | **KEEP** |
| test_phase6.py | 46 | UNIT | **KEEP** (trim 1) |
| test_production_architecture.py | 83 | INTEGRATION | **KEEP** |
| test_provider_phase4_2.py | 24 | UNIT | **KEEP** (trim 1) |
| test_rate_limit.py | 9 | UNIT | **KEEP** |
| test_razorpay.py | 34 | INTEGRATION/SECURITY | **KEEP** |
| test_razorpay_integration.py | 17 | INTEGRATION | **KEEP** |
| test_real_providers.py | 6 | REAL_PROVIDER | **NEW** |
| test_approval_gate.py | 61 | UNIT | **NEW** |

**Total: 563 tests in 14 files** (down from 597 in 16 files, removing 34 exact duplicates + 4 files)

---

## 10. FILES SAFE TO EVENTUALLY MERGE

| Current File | Merge Into | Tests Migrated |
|-------------|-----------|----------------|
| test_phase7_real.py | test_real_providers.py | 6 (from 28) |
| test_phase7_3_real.py | test_real_providers.py | 4 (from 21) |
| test_phase7_1.py | test_approval_gate.py | 27 (from 29) |
| test_phase7_2.py | test_approval_gate.py | 35 (from 37) |

**After merge, these 4 files can be deleted.**

### Duplicates safe to remove (without merging):

| File | Test | Reason |
|------|------|--------|
| test_phase6.py | TestProviderParity | Duplicate of provider_phase4_2.py |
| test_provider_phase4_2.py | TestSafetyGuarantees::test_provider_failure_does_not_cause_approval | Duplicate of phase7_1 |

---

## 11. SUMMARY

| Metric | Before | After |
|--------|--------|-------|
| Test files | 16 | 14 |
| Total tests | 597 | ~563 |
| Duplicate tests removed | 0 | ~34 |
| Phase files deleted | 0 | 4 |
| New consolidated files | 0 | 2 |
| Real-provider tests | 49 (scattered) | 6 (consolidated) |
| Approval gate tests | 27 (4 files) | 61 (1 file, deduplicated) |

**No production code changes. No behavior changes. No commits.**
