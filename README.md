# EntitlementLedger

> "Payments tell you what happened. EntitlementLedger reconstructs why."

A financial decision-provenance system for marketplace finance teams. Reconstructs the evidence, policy, and approval chain behind seller financial decisions.

## Quick Start

### Local Development

1. **Start Ollama** (local AI provider — free, no API key):
   ```bash
   ollama serve
   ollama pull qwen3.5
   ```

2. **Start Backend**:
   ```bash
   cd backend
   pip install -r requirements.txt
   python run.py
   # API: http://localhost:8000
   # Swagger: http://localhost:8000/docs
   ```

3. **Start Frontend**:
   ```bash
   cd frontend
   npm install
   npm run dev
   # App: http://localhost:5173
   ```

4. **Optional: Configure Razorpay Test Mode**:
   ```bash
   cp .env.example .env
   # Edit .env and add:
   # RAZORPAY_KEY_ID=rzp_test_...
   # RAZORPAY_KEY_SECRET=...
   # RAZORPAY_WEBHOOK_SECRET=...
   ```

5. **Use Local Webhook Simulator** (when no public HTTPS endpoint):
   - Open the Razorpay Events page in the app
   - Click "Simulate" to send test events through the ingestion pipeline
   - Or call `POST /api/webhooks/razorpay/simulate` directly

### Tests

```bash
cd backend
python -m pytest -v  # 104 tests, all offline
```

## Architecture

```
Backend (Python/FastAPI/SQLite)
├── main.py              # FastAPI app with CORS + routers
├── database.py          # SQLite schema and connection
├── models.py            # Pydantic request/response models
├── calculations.py      # Deterministic financial engine
├── hash_chain.py        # Tamper-evident SHA-256 chain
├── seed_data.py         # 5 demo scenarios
├── routes.py            # Core API endpoints
├── razorpay_routes.py   # Webhook ingestion + simulator + events API
├── razorpay_client.py   # Minimal Razorpay REST client
├── razorpay_events.py   # In-memory event store (idempotent)
├── ai/
│   ├── llm_provider.py  # Abstract LLM interface (Ollama/Claude)
│   ├── extraction.py    # Evidence extraction via LLM
│   ├── reasoning.py     # Claim reasoning via LLM
│   ├── pipeline.py      # Full AI pipeline orchestrator
│   └── test_mocks.py    # Mock responses for offline tests
└── tests/               # 104 tests across 3 files

Frontend (React/TypeScript/Vite/Tailwind)
└── src/
    ├── api/             # Types and API client
    ├── lib/             # Formatting utilities
    └── pages/           # Dashboard, DecisionDetail, AuditTrail,
                         # SellerProfile, EvidenceView, DefensePacket,
                         # RazorpayEvents
```

## Key Design Decisions

1. **AI does NOT determine amounts** — The LLM extracts facts and claims. Deterministic Python functions calculate all financial amounts.

2. **Tamper-evident hash chain** — Each decision's hash includes the previous decision's hash. Content canonicalization ensures deterministic hashing.

3. **Evidence-linked decisions** — Every deduction links to specific evidence records and policy clauses.

4. **Defense Packet** — Complete evidence package for dispute resolution including financial breakdown, all evidence, applicable policies, and integrity verification.

5. **Pluggable LLM providers** — Ollama (local/free) is the default. Anthropic Claude is optional. No API key required for demo.

6. **Razorpay as evidence source** — Webhook events become financial evidence records. The AI interprets them alongside operational evidence.

## Scenarios

| # | Name | What It Shows |
|---|------|--------------|
| 1 | Return + SLA Breach | Primary: ₹100K → ₹75K with 3 deductions |
| 2 | Late Delivery | Platform fee + SLA penalty |
| 3 | Complaint No Penalty | AI determines deduction is NOT justified |
| 4 | Multiple Decisions | Same seller, different orders |
| 5 | Tampered Decision | Hash chain integrity detection |

## API Endpoints

### Core
- `GET /api/stats` — Dashboard statistics
- `GET /api/decisions` — List all decisions
- `GET /api/decisions/{id}` — Decision detail
- `GET /api/decisions/{id}/evidence` — Linked evidence
- `GET /api/decisions/{id}/verify` — Verify hash chain
- `GET /api/decisions/{id}/defense-packet` — Defense packet
- `GET /api/sellers/{id}/decisions` — Seller decision history
- `POST /api/scenarios/{id}/run` — Run a scenario

### AI
- `GET /api/ai/status` — Active AI provider info

### Razorpay
- `POST /api/webhooks/razorpay` — Webhook ingestion (raw-byte HMAC-SHA256 verification)
- `POST /api/webhooks/razorpay/simulate` — Local webhook simulator
- `GET /api/razorpay/events` — List stored financial events
- `GET /api/razorpay/events/{id}` — Get event detail
- `POST /api/razorpay/events/{id}/process` — Process event into evidence + decision
- `GET /api/razorpay/connection` — Razorpay configuration status
- `GET /api/razorpay/status` — Integration status (live/demo)

## Razorpay → Ledger Flow

```
Razorpay Event (webhook/API/simulator)
  ↓
Canonical Event (normalized, source-tagged)
  ↓
Evidence Record (deterministic fact extraction)
  ↓
Financial Claims (platform fee from policy)
  ↓
Deterministic Calculation (gross → deductions → final)
  ↓
Decision (with hash chain)
  ↓
Audit Trail / Defense Packet
```

### Processing a Razorpay Event

```bash
# 1. Simulate a payment event
POST /api/webhooks/razorpay/simulate
{"event_type": "payment.captured", "amount": 100000}

# 2. Process into ledger
POST /api/razorpay/events/{event_id}/process
→ creates Evidence + Decision + links to hash chain

# 3. View the decision
GET /api/decisions/{decision_id}
→ shows Razorpay source metadata, financial breakdown, hash
```

### Security

- **Webhook verification**: Raw-byte HMAC-SHA256 with constant-time comparison
- **Missing secret**: HTTP 503 (webhook rejected, no event created)
- **Invalid signature**: HTTP 401
- **Unverified live webhooks**: Cannot be processed into evidence (HTTP 403)
- **Simulator**: Always tagged `source=local_simulator`, works without credentials
- **Secrets**: Never exposed in API responses, logs, or evidence records

## Live Razorpay vs Local Simulator

| | Live Razorpay Webhook | Local Webhook Simulator |
|---|---|---|
| **Source** | Real Razorpay Test Mode | `POST /api/webhooks/razorpay/simulate` |
| **Requires** | Public HTTPS endpoint + credentials | Nothing |
| **Signature** | Verified via RAZORPAY_WEBHOOK_SECRET | N/A (rejected if no secret) |
| **Use case** | Production integration | Development, demo, hackathon |
| **Endpoint** | `POST /api/webhooks/razorpay` | `POST /api/webhooks/razorpay/simulate` |

## Environment Variables

See `.env.example` for all configuration options. Key variables:

- `OLLAMA_MODEL` — LLM model name (default: `qwen3.5:latest`)
- `OLLAMA_BASE_URL` — Ollama server URL (default: `http://localhost:11434`)
- `ANTHROPIC_API_KEY` — Optional: use Claude instead of Ollama
- `RAZORPAY_KEY_ID` — Optional: Razorpay test-mode key
- `RAZORPAY_KEY_SECRET` — Optional: Razorpay secret
- `RAZORPAY_WEBHOOK_SECRET` — Optional: Webhook signature verification