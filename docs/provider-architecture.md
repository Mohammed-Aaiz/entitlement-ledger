# Provider Architecture — Finance Controller

## Overview

The Finance Controller is **provider-independent**. The LLM layer uses a pluggable provider abstraction with deliberately different deployment roles.

```
                    FINANCE CONTROLLER
                           │
                    Provider Interface
                           │
             ┌─────────────┼─────────────┐
             │             │             │
          Ollama          Groq         Gemini
           LOCAL        PRODUCTION     EVIDENCE
           SOLO          CONTROLLER   INTELLIGENCE
             │             │             │
             └─────────────┼─────────────┘
                           ↓
                    Validated Evidence
                           ↓
                      Policy Engine
                           ↓
                Deterministic Calculation
                           ↓
                  Decision + Audit Chain
```

**The LLM NEVER becomes the financial authority.**

---

## Provider Roles

### Ollama — Local Development Only

- **Role**: Full solo execution of the Finance Controller LLM layer
- **Deployment**: Local development machine only
- **Capabilities**: `complete()`, `complete_json()`, `complete_with_tools()`, `chat_complete()`
- **API Key**: None required
- **Vercel**: **NEVER** used in production

**Ollama is intentionally local-only and is not part of the Vercel production deployment.**

Configuration:
```bash
OLLAMA_MODEL=qwen3.5:latest
OLLAMA_BASE_URL=http://localhost:11434
```

### Groq — Primary Cloud Controller

- **Role**: Controller/orchestrator for the Finance Controller Agent
- **Deployment**: Vercel production (primary)
- **Capabilities**: Native tool calling, evidence retrieval decisions, case investigation, final structured analysis
- **API Key**: `GROQ_API_KEY` required
- **Model**: `openai/gpt-60b`

Groq is responsible for:
1. Determining required evidence
2. Selecting tools
3. Retrieving evidence
4. Detecting ambiguity/conflict
5. Synthesizing validated facts
6. Producing final structured claim analysis

Groq MUST NOT:
- Calculate money
- Approve money movement
- Modify policies
- Override deterministic results

### Gemini — Evidence Intelligence Specialist

- **Role**: Complex/unstructured evidence analysis
- **Deployment**: Vercel production (secondary)
- **Capabilities**: Document interpretation, evidence cross-checking, structured fact extraction
- **API Key**: `GEMINI_API_KEY` required
- **Model**: `gemini-2.5-flash`

Gemini is invoked only when:
- Invoice PDF analysis
- Delivery document interpretation
- Return document analysis
- Seller-uploaded document processing
- Long unstructured evidence
- Conflicting textual evidence
- Document cross-checking

Gemini returns structured evidence observations:
```json
{
  "facts": [...],
  "document_ids": [...],
  "contradictions": [...],
  "confidence": 0.94
}
```

Gemini MUST NOT directly determine:
- Entitlement amounts
- Fee amounts
- Payout amounts
- Approval status

### Intelligent Routing

```
Simple structured Razorpay event → Groq only
Complex document evidence → Groq orchestrates → Gemini analyzes → Groq continues
Conflicting evidence → Groq detects → Gemini cross-check → unresolved → REVIEW_REQUIRED
```

---

## Vercel Safety

Production deployment **NEVER** attempts localhost Ollama.

The factory function `get_provider()` checks for production mode:

```python
def is_production() -> bool:
    return (
        os.environ.get("VERCEL") is not None
        or os.environ.get("PRODUCTION_PROVIDER") is not None
        or os.environ.get("ENVIRONMENT", "").lower() in ("production", "prod")
    )
```

When `is_production() == True`:
- `PRODUCTION_PROVIDER` must be explicitly set (default: `groq`)
- Only cloud providers (groq, gemini) are attempted
- `PRODUCTION_PROVIDER=ollama` raises `EnvironmentError` immediately
- No localhost connections are attempted

---

## Provider Parity

All providers implement the same interface:
- `complete()` — single-turn text completion
- `complete_json()` — structured JSON completion
- `complete_with_tools()` — native tool/function calling
- `chat_complete()` — multi-turn conversation

The Finance Controller agent calls only provider-neutral methods. Provider-specific API behavior belongs only inside provider adapters.

---

## Error Handling

Provider failures are classified into safe categories:
- `provider_error` — General provider failure
- `rate_limit` — Rate limit / quota exceeded (429)
- `timeout` — Request timed out
- `schema_error` — Structured output validation failure
- `tool_error` — Tool calling error
- `invalid_arguments` — Invalid tool arguments
- `unavailable` — Provider not available

When safe analysis cannot be completed: `REVIEW_REQUIRED`. Never auto-approve.

---

## Deployment Configuration

### Vercel Production

```bash
PRODUCTION_PROVIDER=groq
GROQ_API_KEY=gsk_...
GEMINI_API_KEY=...  # Optional secondary
```

### Local Development

```bash
OLLAMA_MODEL=qwen3.5:latest
OLLAMA_BASE_URL=http://localhost:11434
```

Or use cloud providers locally:
```bash
GROQ_API_KEY=gsk_...
```

### Benchmark

```bash
# Mock mode (CI, no API key)
python -m benchmark --records 100 --seed 42

# Real Groq
python -m benchmark --records 100 --seed 42 --provider groq --model openai/gpt-oss-120b

# Real Gemini
python -m benchmark --records 100 --seed 42 --provider gemini --model gemini-2.5-flash

# Real Ollama (local only)
python -m benchmark --records 100 --seed 42 --provider ollama
```

---

## Security

Every provider output is untrusted:
- Server-controlled tenant ID
- Tenant-scoped tools
- Evidence treated as untrusted data
- Policy ID allowlisting
- Evidence ID validation
- Tool argument validation
- No secrets in prompts/reports
- No PII leakage
- Bounded execution
