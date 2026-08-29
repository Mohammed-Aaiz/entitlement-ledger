"""EntitlementLedger — FastAPI application.

Production-ready configuration:
- Authentication via JWT (auth_routes.py)
- SQLite persistence with tenant isolation (database.py)
- PostgreSQL support when DATABASE_URL is set
- Dev-only seed data (loaded when SEED_DATA=true or ENV=development)
- Razorpay webhook integration (razorpay_routes.py)
- Audit logging on all mutations
- Structured logging with request IDs
- Health and readiness endpoints
"""
import os
import json
import logging
import uuid
import time
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from database import init_db, get_db, log_audit, close_db, check_db_health
from rate_limit import RateLimitMiddleware

# Load .env
_backend_dir = Path(__file__).parent
_dotenv_path = _backend_dir / ".env"
if _dotenv_path.exists():
    load_dotenv(_dotenv_path)

logger = logging.getLogger(__name__)

ENV = os.environ.get("ENV", "development")
SEED_DATA = os.environ.get("SEED_DATA", "").lower() in ("true", "1", "yes")

# --- Structured logging setup ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

# Secrets that must NEVER be logged or exposed
_SENSITIVE_KEYS = {"password", "password_hash", "key_secret", "webhook_secret",
                   "api_key", "secret_key", "token", "access_token"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and optionally seed dev data."""
    await init_db()

    # Always ensure required system configuration exists (policies, scenarios).
    # This is idempotent — safe to call on every startup.
    await _ensure_system_config()

    # Production NEVER seeds demo data. Requires SEED_DATA=true explicitly.
    if ENV != "production" and (SEED_DATA or ENV == "development"):
        await _seed_dev_data()

    yield
    await close_db()


async def _seed_dev_data():
    """Load seed scenarios, policies, and demo decisions into the database.

    Only runs in development mode. Creates a 'demo' tenant with a demo user
    and loads the 5 seeded scenarios + their evidence and decisions.
    """
    import hashlib
    from datetime import datetime, timedelta

    from database import DB_PATH

    db = await get_db()
    try:
        # Check if data already seeded
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM policies")
        row = await cursor.fetchone()
        if row["cnt"] > 0:
            logger.info("Seed data already present, skipping.")
            return

        # Create demo tenant
        await db.execute(
            "INSERT OR IGNORE INTO tenants (tenant_id, name) VALUES (?, ?)",
            ("demo", "Demo Organization"),
        )

        # Create demo admin user (password: "demo1234")
        from auth import hash_password
        user_id = "usr_demo_admin"
        demo_hash = hash_password("demo1234")
        now = datetime.utcnow().isoformat()
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, email, password_hash, display_name, role, tenant_id, created_at, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, "admin@demo.ledger", demo_hash, "Demo Admin", "admin", "demo", now, True),
        )

        # Load policies
        from seed_data import POLICY_RECORDS, EVIDENCE_RECORDS, SCENARIO_EVIDENCE_MAP, SCENARIO_POLICY_MAP

        for p in POLICY_RECORDS:
            await db.execute(
                "INSERT OR IGNORE INTO policies (policy_id, version, clause_text, effective_date) VALUES (?, ?, ?, ?)",
                (p["policy_id"], p["version"], p["clause_text"], p["effective_date"]),
            )

        # Load evidence records (scoped to demo tenant)
        for ev in EVIDENCE_RECORDS:
            extracted = ev.get("extracted_facts", "[]")
            linked = ev.get("linked_decision_ids", "[]")
            await db.execute(
                "INSERT OR IGNORE INTO evidence (evidence_id, tenant_id, source_type, raw_content, extracted_facts, linked_decision_ids, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ev["evidence_id"], "demo", ev["source_type"], ev["raw_content"],
                 extracted if isinstance(extracted, str) else json.dumps(extracted),
                 linked if isinstance(linked, str) else json.dumps(linked),
                 datetime.now().isoformat()),
            )

        # Load scenarios
        scenarios = [
            ("scenario_1", "Return + SLA Breach", "Platform fee, SLA penalty for late delivery, and return reserve for processed return.", "completed", '["platform_1_1", "sla_4_2", "returns_3_1"]'),
            ("scenario_2", "Late Delivery Only", "Platform fee and SLA penalty for delivery delay. No returns.", "completed", '["platform_1_1", "sla_4_2"]'),
            ("scenario_3", "Complaint Without Penalty", "Customer complaint filed but evidence does not justify additional deduction.", "completed", '["platform_1_1"]'),
            ("scenario_4", "Multiple Seller Decisions", "Second decision for seller_abc showing decision history.", "completed", '["platform_1_1"]'),
            ("scenario_5", "Tampered Decision", "Record modified after hashing, breaking the integrity chain.", "completed", '["platform_1_1", "sla_4_2", "returns_3_1"]'),
        ]
        for sid, name, desc, status, policy_ids in scenarios:
            await db.execute(
                "INSERT OR IGNORE INTO scenarios (scenario_id, name, description, status, policy_ids) VALUES (?, ?, ?, ?, ?)",
                (sid, name, desc, status, policy_ids),
            )

        # Load seeded decisions (hash chain)
        from seed_data import get_all_decisions
        decisions = get_all_decisions()
        for d in decisions:
            await db.execute(
                "INSERT OR IGNORE INTO decisions "
                "(decision_id, tenant_id, entity_type, entity_id, gross_amount, line_items, final_amount, "
                "policy_version_id, approver_id, approved_at, model_output, prev_decision_hash, decision_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    d["decision_id"], "demo", d["entity_type"], d["entity_id"],
                    d["gross_amount"], json.dumps(d["line_items"]),
                    d["final_amount"], d["policy_version_id"],
                    d["approver_id"], d["approved_at"],
                    json.dumps(d.get("model_output", {})),
                    d["prev_decision_hash"], d["decision_hash"],
                    d["created_at"], d.get("status", "APPROVED"),
                ),
            )

        await db.commit()
        logger.info("Development seed data loaded: %d policies, %d evidence, %d scenarios, %d decisions",
                     len(POLICY_RECORDS), len(EVIDENCE_RECORDS), len(scenarios), len(decisions))
    finally:
        await db.close()


async def _ensure_system_config():
    """Idempotently insert required system configuration (policies and scenarios).

    These are NOT demo data — they are business rules and analysis templates
    that the application requires to function. Safe to call on every startup.
    """
    from seed_data import POLICY_RECORDS

    db = await get_db()
    try:
        # Insert policies idempotently
        for p in POLICY_RECORDS:
            await db.execute(
                "INSERT OR IGNORE INTO policies (policy_id, version, clause_text, effective_date) "
                "VALUES (?, ?, ?, ?)",
                (p["policy_id"], p["version"], p["clause_text"], p["effective_date"]),
            )

        # Insert scenarios with policy_ids — the analysis templates
        system_scenarios = [
            {
                "scenario_id": "scenario_1",
                "name": "Return + SLA Breach",
                "description": "Analyzes evidence for platform fee, SLA penalty for late delivery, and return reserve.",
                "policy_ids": json.dumps(["platform_1_1", "sla_4_2", "returns_3_1"]),
            },
            {
                "scenario_id": "scenario_2",
                "name": "Late Delivery Only",
                "description": "Analyzes evidence for platform fee and SLA penalty for delivery delay.",
                "policy_ids": json.dumps(["platform_1_1", "sla_4_2"]),
            },
            {
                "scenario_id": "scenario_3",
                "name": "Complaint Without Penalty",
                "description": "Analyzes evidence for platform fee only — complaint does not justify additional deduction.",
                "policy_ids": json.dumps(["platform_1_1"]),
            },
            {
                "scenario_id": "scenario_4",
                "name": "Multiple Seller Decisions",
                "description": "Second decision for same seller showing decision history.",
                "policy_ids": json.dumps(["platform_1_1"]),
            },
            {
                "scenario_id": "scenario_5",
                "name": "Tampered Decision",
                "description": "A decision where stored content was modified after hashing, breaking the integrity chain.",
                "policy_ids": json.dumps(["platform_1_1", "sla_4_2", "returns_3_1"]),
            },
        ]
        for s in system_scenarios:
            await db.execute(
                "INSERT OR IGNORE INTO scenarios (scenario_id, name, description, status, policy_ids) "
                "VALUES (?, ?, ?, 'active', ?)",
                (s["scenario_id"], s["name"], s["description"], s["policy_ids"]),
            )

        await db.commit()
        logger.info("System configuration ensured: %d policies, %d scenarios",
                     len(POLICY_RECORDS), len(system_scenarios))
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="EntitlementLedger",
    description="Financial decision-provenance system for marketplace finance teams.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(RateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get(
            "CORS_ORIGINS",
            "http://localhost:5173,http://localhost:3000",  # Dev-only default; production MUST set CORS_ORIGINS
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Structured request logging with request IDs and timing."""
    request_id = str(uuid.uuid4())[:12]
    start = time.time()
    response = await call_next(request)
    duration_ms = int((time.time() - start) * 1000)
    if request.url.path.startswith("/api/"):
        # Never log request bodies (may contain secrets)
        logger.info(
            "[rid=%s] %s %s -> %s (%dms)",
            request_id, request.method, request.url.path,
            response.status_code, duration_ms,
        )
    # Attach request ID header for traceability
    response.headers["X-Request-ID"] = request_id
    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if ENV == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# Global error handler — never expose stack traces or secrets to clients
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# Import and mount routers
from routes import router
from razorpay_routes import router as razorpay_router
from auth_routes import router as auth_router

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(router, prefix="/api")
app.include_router(razorpay_router, prefix="/api")


@app.get("/")
async def root():
    return {
        "name": "EntitlementLedger",
        "version": "0.2.0",
        "description": "Financial decision-provenance system",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Lightweight health check — no auth required, no expensive calls."""
    return {"status": "ok", "version": "0.2.0"}


@app.get("/ready")
async def ready():
    """Readiness check — verifies database and configuration.

    Returns 200 when the service can accept traffic,
    503 when it cannot.
    """
    checks = {}
    all_ok = True

    # Database check
    db_ok = await check_db_health()
    checks["database"] = "ok" if db_ok else "unavailable"
    if not db_ok:
        all_ok = False

    # Razorpay configuration check
    from razorpay_client import is_configured as rp_configured
    checks["razorpay"] = "configured" if rp_configured() else "not_configured"

    # AI provider check
    from ai.llm_provider import is_ai_available
    checks["ai"] = "available" if is_ai_available() else "unavailable"

    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if all_ok else "not_ready", "checks": checks},
    )



