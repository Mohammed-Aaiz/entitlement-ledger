"""Production database layer for EntitlementLedger.

Supports:
- PostgreSQL (production, via DATABASE_URL)
- SQLite (development fallback, via EL_DB_PATH or auto-created file)

Schema includes all required tables with proper foreign keys and indexes.
Multi-tenant isolation is enforced at the query level.
"""
import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ENV = os.environ.get("ENV", "development")

# If DATABASE_URL is set, use PostgreSQL; otherwise fall back to SQLite
USE_POSTGRES = bool(DATABASE_URL) and not DATABASE_URL.startswith("sqlite")

DB_PATH = os.environ.get(
    "EL_DB_PATH",
    os.path.join(os.path.dirname(__file__), "entitlement_ledger.db"),
)


# ---------------------------------------------------------------------------
# PostgreSQL schema (applied via Alembic migrations in production)
# ---------------------------------------------------------------------------

POSTGRES_SCHEMA = """
-- Tenants for multi-tenant isolation
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id VARCHAR(128) PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Users with tenant FK and role-based access
CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(128) PRIMARY KEY,
    email VARCHAR(256) UNIQUE NOT NULL,
    password_hash VARCHAR(256) NOT NULL,
    display_name VARCHAR(256) NOT NULL,
    role VARCHAR(64) NOT NULL DEFAULT 'analyst',
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);

-- Decisions with tenant isolation and hash chain
CREATE TABLE IF NOT EXISTS decisions (
    decision_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    entity_type VARCHAR(64) NOT NULL,
    entity_id VARCHAR(256) NOT NULL,
    gross_amount BIGINT NOT NULL,
    line_items JSONB NOT NULL DEFAULT '[]',
    final_amount BIGINT NOT NULL,
    policy_version_id VARCHAR(256) NOT NULL,
    approver_id VARCHAR(128) NOT NULL,
    approved_at TIMESTAMPTZ,
    model_output JSONB NOT NULL DEFAULT '{}',
    prev_decision_hash VARCHAR(128) NOT NULL,
    decision_hash VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status VARCHAR(32) NOT NULL DEFAULT 'REVIEW_REQUIRED'
);
CREATE INDEX IF NOT EXISTS idx_decisions_tenant ON decisions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_decisions_entity ON decisions(tenant_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_decisions_hash ON decisions(decision_hash);
CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status);

-- Evidence records with tenant isolation
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    source_type VARCHAR(64) NOT NULL,
    raw_content TEXT NOT NULL,
    extracted_facts JSONB NOT NULL DEFAULT '[]',
    linked_decision_ids JSONB NOT NULL DEFAULT '[]',
    ai_analyzed BOOLEAN NOT NULL DEFAULT FALSE,
    content_hash VARCHAR(128) NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_evidence_tenant ON evidence(tenant_id);
CREATE INDEX IF NOT EXISTS idx_evidence_hash ON evidence(content_hash);

-- Policies (shared across tenants, scoped by policy_id)
CREATE TABLE IF NOT EXISTS policies (
    policy_id VARCHAR(128) PRIMARY KEY,
    version VARCHAR(32) NOT NULL,
    clause_text TEXT NOT NULL,
    effective_date VARCHAR(32) NOT NULL,
    expiration_date VARCHAR(32),
    tenant_id VARCHAR(128)
);

-- Scenarios (dev/test only — not production data)
CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id VARCHAR(128) PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    description TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    policy_ids JSONB NOT NULL DEFAULT '[]'
);

-- Razorpay events with tenant isolation
CREATE TABLE IF NOT EXISTS razorpay_events (
    event_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    event_type VARCHAR(64) NOT NULL,
    source VARCHAR(32) NOT NULL DEFAULT 'local_simulator',
    verification_status VARCHAR(32) NOT NULL DEFAULT 'unverified',
    razorpay_entity_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
    razorpay_entity_id VARCHAR(256) NOT NULL DEFAULT '',
    payment_id VARCHAR(128) NOT NULL DEFAULT '',
    order_id VARCHAR(128) NOT NULL DEFAULT '',
    amount BIGINT,
    currency VARCHAR(8) NOT NULL DEFAULT 'INR',
    status VARCHAR(64) NOT NULL DEFAULT 'unknown',
    event_timestamp TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL,
    extracted_facts JSONB NOT NULL DEFAULT '[]',
    linked_decision_id VARCHAR(128),
    payload_hash VARCHAR(128) NOT NULL DEFAULT '',
    raw_payload JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_rp_events_tenant ON razorpay_events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rp_events_hash ON razorpay_events(payload_hash);
CREATE INDEX IF NOT EXISTS idx_rp_events_source ON razorpay_events(source);

-- Razorpay API-synced orders
CREATE TABLE IF NOT EXISTS razorpay_orders (
    order_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    entity_id VARCHAR(128),
    amount BIGINT NOT NULL,
    currency VARCHAR(8) NOT NULL DEFAULT 'INR',
    status VARCHAR(64) NOT NULL,
    receipt TEXT,
    notes JSONB DEFAULT '{}',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_updated_at TIMESTAMPTZ,
    raw_payload JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_rp_orders_tenant ON razorpay_orders(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rp_orders_entity ON razorpay_orders(entity_id);
CREATE INDEX IF NOT EXISTS idx_rp_orders_status ON razorpay_orders(status);

-- Razorpay API-synced payments
CREATE TABLE IF NOT EXISTS razorpay_payments (
    payment_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    order_id VARCHAR(128),
    entity_id VARCHAR(128),
    amount BIGINT NOT NULL,
    currency VARCHAR(8) NOT NULL DEFAULT 'INR',
    status VARCHAR(64) NOT NULL,
    method VARCHAR(64),
    captured BOOLEAN DEFAULT FALSE,
    amount_refunded BIGINT DEFAULT 0,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_updated_at TIMESTAMPTZ,
    raw_payload JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_rp_payments_tenant ON razorpay_payments(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rp_payments_order ON razorpay_payments(order_id);
CREATE INDEX IF NOT EXISTS idx_rp_payments_entity ON razorpay_payments(entity_id);
CREATE INDEX IF NOT EXISTS idx_rp_payments_status ON razorpay_payments(status);

-- Razorpay API-synced settlements
CREATE TABLE IF NOT EXISTS razorpay_settlements (
    settlement_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    amount BIGINT NOT NULL,
    currency VARCHAR(8) NOT NULL DEFAULT 'INR',
    status VARCHAR(64) NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_updated_at TIMESTAMPTZ,
    raw_payload JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_rp_settlements_tenant ON razorpay_settlements(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rp_settlements_status ON razorpay_settlements(status);

-- Razorpay sync metadata
CREATE TABLE IF NOT EXISTS razorpay_sync_metadata (
    sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id VARCHAR(128) NOT NULL,
    sync_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    records_synced INTEGER DEFAULT 0,
    records_failed INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

-- Audit log — append-only
CREATE TABLE IF NOT EXISTS audit_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id VARCHAR(128) NOT NULL,
    user_id TEXT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    ip_address TEXT,
    request_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_log(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

-- Razorpay account-to-tenant mapping
CREATE TABLE IF NOT EXISTS razorpay_account_mappings (
    account_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    webhook_secret VARCHAR(256),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Normalized financial records for reconciliation (payments, refunds,
-- settlements, fee/tax, adjustments).  All amounts in integer subunits (paise).
CREATE TABLE IF NOT EXISTS reconciliation_records (
    record_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    record_type VARCHAR(32) NOT NULL,
    external_id VARCHAR(256) NOT NULL,
    amount BIGINT NOT NULL,
    currency VARCHAR(8) NOT NULL DEFAULT 'INR',
    status VARCHAR(64) NOT NULL DEFAULT 'unknown',
    payment_id VARCHAR(128) NOT NULL DEFAULT '',
    order_id VARCHAR(128) NOT NULL DEFAULT '',
    fee_amount BIGINT NOT NULL DEFAULT 0,
    tax_amount BIGINT NOT NULL DEFAULT 0,
    adjustment_sign VARCHAR(16) NOT NULL DEFAULT '',
    recorded_at TIMESTAMPTZ,
    source VARCHAR(32) NOT NULL DEFAULT 'batch',
    raw_evidence_ref VARCHAR(256) NOT NULL DEFAULT '',
    payload_hash VARCHAR(128) NOT NULL DEFAULT '',
    extra JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rec_records_tenant ON reconciliation_records(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rec_records_payment ON reconciliation_records(tenant_id, payment_id);
CREATE INDEX IF NOT EXISTS idx_rec_records_type ON reconciliation_records(record_type);

-- One reconciliation case per payment (deterministic outcome + audit trail)
CREATE TABLE IF NOT EXISTS reconciliation_cases (
    case_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    run_id VARCHAR(128) NOT NULL DEFAULT '',
    payment_id VARCHAR(128) NOT NULL,
    related_record_ids JSONB NOT NULL DEFAULT '[]',
    expected_amount BIGINT NOT NULL DEFAULT 0,
    actual_amount BIGINT NOT NULL DEFAULT 0,
    variance BIGINT NOT NULL DEFAULT 0,
    classification VARCHAR(32) NOT NULL DEFAULT 'REVIEW_REQUIRED',
    exception_codes JSONB NOT NULL DEFAULT '[]',
    exceptions JSONB NOT NULL DEFAULT '[]',
    ai_status VARCHAR(32) NOT NULL DEFAULT 'not_needed',
    ai_invoked BOOLEAN NOT NULL DEFAULT FALSE,
    ai_confidence DOUBLE PRECISION,
    ai_interpretation JSONB NOT NULL DEFAULT '{}',
    ai_technical_reason TEXT NOT NULL DEFAULT '',
    ai_trigger_reason TEXT NOT NULL DEFAULT '',
    ai_tool_calls INTEGER NOT NULL DEFAULT 0,
    calculation_trace JSONB NOT NULL DEFAULT '{}',
    match_info JSONB NOT NULL DEFAULT '{}',
    tier_analysis JSONB NOT NULL DEFAULT '{}',
    decision_id VARCHAR(128) NOT NULL DEFAULT '',
    explanation TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rec_cases_tenant ON reconciliation_cases(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rec_cases_run ON reconciliation_cases(tenant_id, run_id);
CREATE INDEX IF NOT EXISTS idx_rec_cases_class ON reconciliation_cases(classification);
CREATE INDEX IF NOT EXISTS idx_rec_cases_payment ON reconciliation_cases(tenant_id, payment_id);

-- Settlement recon data (Tier 3 linkage + Tier 4 fee/tax evidence).
-- Persisted during Razorpay settlement sync from GET /settlements/{id}/recon.
CREATE TABLE IF NOT EXISTS razorpay_settlement_recon (
    recon_id VARCHAR(192) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    settlement_id VARCHAR(128) NOT NULL,
    payment_id VARCHAR(128) NOT NULL DEFAULT '',
    order_id VARCHAR(128) NOT NULL DEFAULT '',
    amount BIGINT NOT NULL DEFAULT 0,
    fee BIGINT NOT NULL DEFAULT 0,
    tax BIGINT NOT NULL DEFAULT 0,
    recon_type VARCHAR(32) NOT NULL DEFAULT '',
    recorded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_recon_settlement ON razorpay_settlement_recon(tenant_id, settlement_id);
CREATE INDEX IF NOT EXISTS idx_recon_payment ON razorpay_settlement_recon(tenant_id, payment_id);

-- Batch reconciliation run with real aggregate metrics
CREATE TABLE IF NOT EXISTS reconciliation_runs (
    run_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    source VARCHAR(32) NOT NULL DEFAULT 'batch',
    total_records INTEGER NOT NULL DEFAULT 0,
    total_cases INTEGER NOT NULL DEFAULT 0,
    matched INTEGER NOT NULL DEFAULT 0,
    review_required INTEGER NOT NULL DEFAULT 0,
    exceptions INTEGER NOT NULL DEFAULT 0,
    match_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    classification_accuracy DOUBLE PRECISION,
    calculation_accuracy DOUBLE PRECISION,
    false_auto_resolve INTEGER NOT NULL DEFAULT 0,
    throughput_per_sec DOUBLE PRECISION NOT NULL DEFAULT 0,
    p50_latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    p95_latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    duplicates_detected INTEGER NOT NULL DEFAULT 0,
    audit_completeness DOUBLE PRECISION NOT NULL DEFAULT 0,
    errors JSONB NOT NULL DEFAULT '[]',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_rec_runs_tenant ON reconciliation_runs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rec_runs_status ON reconciliation_runs(status);

-- Idempotency keys for reconciliation run POSTs (client retry safety).
CREATE TABLE IF NOT EXISTS reconciliation_run_idempotency (
    id SERIAL PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
    idempotency_key VARCHAR(128) NOT NULL,
    run_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_run_idem_run ON reconciliation_run_idempotency(run_id);
"""

# ---------------------------------------------------------------------------
# SQLite schema (development fallback)
# ---------------------------------------------------------------------------

SQLITE_SCHEMA = """
-- Tenants for multi-tenant isolation
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Users with tenant FK and role-based access
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'analyst',
    tenant_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);

-- Decisions with tenant isolation and hash chain
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    gross_amount INTEGER NOT NULL,
    line_items TEXT NOT NULL DEFAULT '[]',
    final_amount INTEGER NOT NULL,
    policy_version_id TEXT NOT NULL,
    approver_id TEXT NOT NULL,
    approved_at TEXT,
    model_output TEXT NOT NULL DEFAULT '{}',
    prev_decision_hash TEXT NOT NULL,
    decision_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_tenant ON decisions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_decisions_entity ON decisions(tenant_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_decisions_hash ON decisions(decision_hash);
CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status);

-- Evidence records with tenant isolation
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    raw_content TEXT NOT NULL,
    extracted_facts TEXT NOT NULL DEFAULT '[]',
    linked_decision_ids TEXT NOT NULL DEFAULT '[]',
    ai_analyzed INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_evidence_tenant ON evidence(tenant_id);
CREATE INDEX IF NOT EXISTS idx_evidence_hash ON evidence(content_hash);

-- Policies (shared across tenants in MVP, scoped by policy_id)
CREATE TABLE IF NOT EXISTS policies (
    policy_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    clause_text TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    expiration_date TEXT,
    tenant_id TEXT
);

-- Scenarios (dev/test only — not production data)
CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    policy_ids TEXT NOT NULL DEFAULT '[]'
);

-- Razorpay events with tenant isolation
CREATE TABLE IF NOT EXISTS razorpay_events (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'local_simulator',
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    razorpay_entity_type TEXT NOT NULL DEFAULT 'unknown',
    razorpay_entity_id TEXT NOT NULL DEFAULT '',
    payment_id TEXT NOT NULL DEFAULT '',
    order_id TEXT NOT NULL DEFAULT '',
    amount INTEGER,
    currency TEXT NOT NULL DEFAULT 'INR',
    status TEXT NOT NULL DEFAULT 'unknown',
    event_timestamp TEXT,
    received_at TEXT NOT NULL,
    extracted_facts TEXT NOT NULL DEFAULT '[]',
    linked_decision_id TEXT,
    payload_hash TEXT NOT NULL DEFAULT '',
    raw_payload TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_rp_events_tenant ON razorpay_events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rp_events_hash ON razorpay_events(payload_hash);
CREATE INDEX IF NOT EXISTS idx_rp_events_source ON razorpay_events(source);

-- Razorpay API-synced orders
CREATE TABLE IF NOT EXISTS razorpay_orders (
    order_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    entity_id TEXT,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    status TEXT NOT NULL,
    receipt TEXT,
    notes TEXT DEFAULT '{}',
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_synced_at TEXT NOT NULL DEFAULT (datetime('now')),
    source_updated_at TEXT,
    raw_payload TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_rp_orders_tenant ON razorpay_orders(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rp_orders_entity ON razorpay_orders(entity_id);
CREATE INDEX IF NOT EXISTS idx_rp_orders_status ON razorpay_orders(status);

-- Razorpay API-synced payments
CREATE TABLE IF NOT EXISTS razorpay_payments (
    payment_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    order_id TEXT,
    entity_id TEXT,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    status TEXT NOT NULL,
    method TEXT,
    captured INTEGER DEFAULT 0,
    amount_refunded INTEGER DEFAULT 0,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_synced_at TEXT NOT NULL DEFAULT (datetime('now')),
    source_updated_at TEXT,
    raw_payload TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_rp_payments_tenant ON razorpay_payments(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rp_payments_order ON razorpay_payments(order_id);
CREATE INDEX IF NOT EXISTS idx_rp_payments_entity ON razorpay_payments(entity_id);
CREATE INDEX IF NOT EXISTS idx_rp_payments_status ON razorpay_payments(status);

-- Razorpay API-synced settlements
CREATE TABLE IF NOT EXISTS razorpay_settlements (
    settlement_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    status TEXT NOT NULL,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_synced_at TEXT NOT NULL DEFAULT (datetime('now')),
    source_updated_at TEXT,
    raw_payload TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_rp_settlements_tenant ON razorpay_settlements(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rp_settlements_status ON razorpay_settlements(status);

-- Razorpay sync metadata
CREATE TABLE IF NOT EXISTS razorpay_sync_metadata (
    sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    sync_type TEXT NOT NULL,
    status TEXT NOT NULL,
    records_synced INTEGER DEFAULT 0,
    records_failed INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

-- Audit log — append-only
CREATE TABLE IF NOT EXISTS audit_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    ip_address TEXT,
    request_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_log(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

-- Razorpay account-to-tenant mapping
CREATE TABLE IF NOT EXISTS razorpay_account_mappings (
    account_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    webhook_secret TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

-- Normalized financial records for reconciliation (payments, refunds,
-- settlements, fee/tax, adjustments).  All amounts in integer subunits (paise).
CREATE TABLE IF NOT EXISTS reconciliation_records (
    record_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    external_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    status TEXT NOT NULL DEFAULT 'unknown',
    payment_id TEXT NOT NULL DEFAULT '',
    order_id TEXT NOT NULL DEFAULT '',
    fee_amount INTEGER NOT NULL DEFAULT 0,
    tax_amount INTEGER NOT NULL DEFAULT 0,
    adjustment_sign TEXT NOT NULL DEFAULT '',
    recorded_at TEXT,
    source TEXT NOT NULL DEFAULT 'batch',
    raw_evidence_ref TEXT NOT NULL DEFAULT '',
    payload_hash TEXT NOT NULL DEFAULT '',
    extra TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_rec_records_tenant ON reconciliation_records(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rec_records_payment ON reconciliation_records(tenant_id, payment_id);
CREATE INDEX IF NOT EXISTS idx_rec_records_type ON reconciliation_records(record_type);

-- One reconciliation case per payment (deterministic outcome + audit trail)
CREATE TABLE IF NOT EXISTS reconciliation_cases (
    case_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    payment_id TEXT NOT NULL,
    related_record_ids TEXT NOT NULL DEFAULT '[]',
    expected_amount INTEGER NOT NULL DEFAULT 0,
    actual_amount INTEGER NOT NULL DEFAULT 0,
    variance INTEGER NOT NULL DEFAULT 0,
    classification TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
    exception_codes TEXT NOT NULL DEFAULT '[]',
    exceptions TEXT NOT NULL DEFAULT '[]',
    ai_status TEXT NOT NULL DEFAULT 'not_needed',
    ai_invoked INTEGER NOT NULL DEFAULT 0,
    ai_confidence REAL,
    ai_interpretation TEXT NOT NULL DEFAULT '{}',
    ai_technical_reason TEXT NOT NULL DEFAULT '',
    ai_trigger_reason TEXT NOT NULL DEFAULT '',
    ai_tool_calls INTEGER NOT NULL DEFAULT 0,
    calculation_trace TEXT NOT NULL DEFAULT '{}',
    match_info TEXT NOT NULL DEFAULT '{}',
    tier_analysis TEXT NOT NULL DEFAULT '{}',
    decision_id TEXT NOT NULL DEFAULT '',
    explanation TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_rec_cases_tenant ON reconciliation_cases(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rec_cases_run ON reconciliation_cases(tenant_id, run_id);
CREATE INDEX IF NOT EXISTS idx_rec_cases_class ON reconciliation_cases(classification);
CREATE INDEX IF NOT EXISTS idx_rec_cases_payment ON reconciliation_cases(tenant_id, payment_id);

-- Batch reconciliation run with real aggregate metrics
CREATE TABLE IF NOT EXISTS reconciliation_runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    source TEXT NOT NULL DEFAULT 'batch',
    total_records INTEGER NOT NULL DEFAULT 0,
    total_cases INTEGER NOT NULL DEFAULT 0,
    matched INTEGER NOT NULL DEFAULT 0,
    review_required INTEGER NOT NULL DEFAULT 0,
    exceptions INTEGER NOT NULL DEFAULT 0,
    match_rate REAL NOT NULL DEFAULT 0,
    classification_accuracy REAL,
    calculation_accuracy REAL,
    false_auto_resolve INTEGER NOT NULL DEFAULT 0,
    throughput_per_sec REAL NOT NULL DEFAULT 0,
    p50_latency_ms REAL NOT NULL DEFAULT 0,
    p95_latency_ms REAL NOT NULL DEFAULT 0,
    duplicates_detected INTEGER NOT NULL DEFAULT 0,
    audit_completeness REAL NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_rec_runs_tenant ON reconciliation_runs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rec_runs_status ON reconciliation_runs(status);

-- Idempotency keys for reconciliation run POSTs.  A client retry (timeout /
-- network failure) with the SAME key returns the ORIGINAL run instead of
-- creating a duplicate run / duplicate cases / duplicate ledger decisions.
CREATE TABLE IF NOT EXISTS reconciliation_run_idempotency (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_run_idem_run ON reconciliation_run_idempotency(run_id);
"""


# ---------------------------------------------------------------------------
# SQL Compatibility Layer
# ---------------------------------------------------------------------------
# Translates SQLite-style SQL to PostgreSQL-compatible SQL:
#   ? params → $1, $2, ...
#   INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
#   json_insert/json_array_length → PostgreSQL jsonb operators
#   datetime('now') → NOW()

def _translate_sql(sql: str, params_count: int = 0) -> str:
    """Translate SQLite-style SQL to PostgreSQL-compatible SQL."""
    if not USE_POSTGRES:
        return sql

    result = sql

    # 1. Replace INSERT OR IGNORE with ON CONFLICT DO NOTHING
    result = re.sub(
        r'INSERT\s+OR\s+IGNORE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)',
        r'INSERT INTO \1 (\2) VALUES (\3) ON CONFLICT DO NOTHING',
        result,
        flags=re.IGNORECASE,
    )

    # 2. Handle json_insert → PostgreSQL jsonb operators
    if 'json_insert' in result:
        # CASE WHEN pattern
        result = re.sub(
            r"CASE WHEN (\w+) = '\[\]' THEN (\?) ELSE json_insert\(\1, '\$\[' \|\| json_array_length\(\1\) \|\| '\]', (\?)\) END",
            r"CASE WHEN \1 = '[]' THEN \2 ELSE \1::jsonb || \3::jsonb END",
            result,
            flags=re.IGNORECASE,
        )
        # Standalone pattern
        result = re.sub(
            r"json_insert\((\w+),\s*'\$\['\s*\|\|\s*json_array_length\((\w+)\)\s*\|\|\s*'\]',\s*(\?)\)",
            r"CASE WHEN \1 = '[]' THEN \3 ELSE \1::jsonb || \3::jsonb END",
            result,
            flags=re.IGNORECASE,
        )
        # Simple indexed pattern
        result = re.sub(
            r"json_insert\((\w+),\s*'\$\[\d+\]',\s*(\?)\)",
            r"\1::jsonb || \2::jsonb",
            result,
            flags=re.IGNORECASE,
        )

    # 3. Replace json_array_length with jsonb_array_length
    result = re.sub(r'json_array_length\(', 'jsonb_array_length(', result, flags=re.IGNORECASE)

    # 4. Replace datetime('now') with NOW()
    result = re.sub(r"datetime\('now'\)", 'NOW()', result, flags=re.IGNORECASE)

    # 5. Replace ? with $1, $2, ... for asyncpg
    if '?' in result:
        counter = [0]
        def _replace_qmark(m):
            counter[0] += 1
            return f'${counter[0]}'
        result = re.sub(r'\?', _replace_qmark, result)

    return result


# ---------------------------------------------------------------------------
# PostgreSQL connection management
# ---------------------------------------------------------------------------

_pg_pool = None


class _PGRow:
    """Wraps asyncpg Record to provide dict-like access compatible with aiosqlite.Row."""

    def __init__(self, record):
        self._record = record

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._record.values())[key]
        return self._record[key]

    def __contains__(self, key):
        return key in self._record

    def __iter__(self):
        return iter(self._record.keys())

    def keys(self):
        return self._record.keys()

    def get(self, key, default=None):
        return self._record.get(key, default)

    def dict(self):
        return dict(self._record)

    def __len__(self):
        return len(self._record)


_ISO_TIMESTAMP_RE = re.compile(
    r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'
)


def _coerce_params(params) -> tuple:
    """Convert Python values to types that asyncpg accepts."""
    if not params:
        return params
    if isinstance(params, (list, tuple)):
        params = list(params)
    else:
        params = [params]
    result = []
    for p in params:
        if isinstance(p, str) and _ISO_TIMESTAMP_RE.match(p):
            try:
                if p.endswith('Z'):
                    result.append(datetime.fromisoformat(p[:-1]).replace(tzinfo=timezone.utc))
                else:
                    result.append(datetime.fromisoformat(p))
            except (ValueError, TypeError):
                result.append(p)
        else:
            result.append(p)
    return tuple(result)


class _PGConnection:
    """Wraps asyncpg connection to provide aiosqlite-compatible interface.

    All execute() calls go through _translate_sql to convert ? to $1,$2...
    and INSERT OR IGNORE → ON CONFLICT DO NOTHING.
    """

    def __init__(self, conn):
        self._conn = conn

    async def execute(self, sql: str, params=None):
        translated = _translate_sql(sql, len(params) if params else 0)
        if params:
            params = _coerce_params(params)
        upper = translated.strip().upper()
        is_select = upper.startswith('SELECT')
        if is_select:
            if params:
                result = await self._conn.fetch(translated, *params)
            else:
                result = await self._conn.fetch(translated)
            return _PGCursorResult(result)
        else:
            # DML (INSERT/UPDATE/DELETE) — use execute() not fetch()
            if params:
                await self._conn.execute(translated, *params)
            else:
                await self._conn.execute(translated)
            return _PGCursorResult([])

    async def executemany(self, sql: str, params_list):
        translated = _translate_sql(sql)
        coerced = [_coerce_params(p) for p in params_list]
        await self._conn.executemany(translated, coerced)
        return self

    async def commit(self):
        # asyncpg in non-autocommit mode needs explicit commit
        try:
            await self._conn.execute('COMMIT')
        except Exception:
            pass

    async def rollback(self):
        try:
            await self._conn.execute('ROLLBACK')
        except Exception:
            pass

    async def close(self):
        # Return connection to pool; on failure, close directly
        try:
            await _pg_pool.release(self._conn)
        except Exception:
            try:
                await self._conn.close()
            except Exception:
                pass


class _PGCursorResult:
    """Result wrapper for PostgreSQL execute calls."""

    def __init__(self, records, status=None):
        self._records = records or []
        self._status = status
        self._idx = 0

    @property
    def rowcount(self):
        return len(self._records)

    @property
    def statusmessage(self):
        return self._status or ''

    async def fetchone(self):
        if self._idx < len(self._records):
            row = self._records[self._idx]
            self._idx += 1
            return _PGRow(row)
        return None

    async def fetchall(self):
        rows = []
        while True:
            row = await self.fetchone()
            if row is None:
                break
            rows.append(row)
        return rows


async def _init_pg_conn(conn):
    """Initialize each new asyncpg connection."""
    # Set statement timeout to prevent hanging
    await conn.execute('SET statement_timeout = 30000')


async def _get_pg_pool():
    """Get or create the PostgreSQL connection pool.

    If the event loop has changed (e.g. TestClient uses a new loop per request),
    close the old pool and create a new one bound to the current loop.
    """
    global _pg_pool
    import asyncio
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _pg_pool is not None:
        # Check if pool's event loop matches the current one
        try:
            pool_loop = _pg_pool._loop
            if pool_loop is not None and pool_loop is not current_loop:
                # Event loop changed — close old pool and recreate
                try:
                    await _pg_pool.close()
                except Exception:
                    pass
                _pg_pool = None
        except AttributeError:
            pass

    if _pg_pool is None:
        import asyncpg
        _pg_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            timeout=30,
            command_timeout=30,
            init=_init_pg_conn,
        )
    return _pg_pool


# ---------------------------------------------------------------------------
# SQLite connection management (development fallback)
# ---------------------------------------------------------------------------

import aiosqlite


# ---------------------------------------------------------------------------
# Unified database interface
# ---------------------------------------------------------------------------

@asynccontextmanager
async def get_db_context():
    """Get a database connection as a context manager.

    Yields a connection-like object that works for both PostgreSQL and SQLite.
    """
    if USE_POSTGRES:
        pool = await _get_pg_pool()
        conn = await pool.acquire()
        try:
            yield _PGConnection(conn)
        finally:
            try:
                await pool.release(conn)
            except Exception:
                try:
                    await conn.close()
                except Exception:
                    pass
    else:
        db = await aiosqlite.connect(DB_PATH, timeout=30)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            await db.close()


async def get_db():
    """Get a database connection.

    Returns a connection that the caller must close.
    """
    if USE_POSTGRES:
        pool = await _get_pg_pool()
        conn = await pool.acquire()
        return _PGConnection(conn)
    else:
        db = await aiosqlite.connect(DB_PATH, timeout=30)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute("PRAGMA foreign_keys=ON")
        return db


async def close_db():
    """Close the database connection pool (called on shutdown)."""
    global _pg_pool
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None


async def _sqlite_migrate(db):
    """Apply schema migrations for existing SQLite databases."""
    migrations = [
        ("evidence", "content_hash", "TEXT NOT NULL DEFAULT ''"),
        ("evidence", "version", "INTEGER NOT NULL DEFAULT 1"),
        ("evidence", "updated_at", "TEXT"),
        ("audit_log", "request_id", "TEXT"),
        ("audit_log", "action", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("audit_log", "entity_type", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("scenarios", "policy_ids", "TEXT NOT NULL DEFAULT '[]'"),
        ("evidence", "ai_analyzed", "INTEGER NOT NULL DEFAULT 0"),
        # 0007 — first-class AI investigation metadata on reconciliation cases
        ("reconciliation_cases", "ai_invoked", "INTEGER NOT NULL DEFAULT 0"),
        ("reconciliation_cases", "ai_trigger_reason", "TEXT NOT NULL DEFAULT ''"),
        ("reconciliation_cases", "ai_tool_calls", "INTEGER NOT NULL DEFAULT 0"),
        # 0008 — tier analysis + settlement recon
        ("reconciliation_cases", "tier_analysis", "TEXT NOT NULL DEFAULT '{}'"),
    ]
    # 0008 — settlement recon table (Tier 3 deterministic linkage + Tier 4 fee/tax evidence)
    try:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS razorpay_settlement_recon ("
            " recon_id TEXT PRIMARY KEY, "
            " tenant_id TEXT NOT NULL, "
            " settlement_id TEXT NOT NULL, "
            " payment_id TEXT NOT NULL DEFAULT '', "
            " order_id TEXT NOT NULL DEFAULT '', "
            " amount INTEGER NOT NULL DEFAULT 0, "
            " fee INTEGER NOT NULL DEFAULT 0, "
            " tax INTEGER NOT NULL DEFAULT 0, "
            " recon_type TEXT NOT NULL DEFAULT '', "
            " recorded_at TEXT, "
            " FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)"
            ")"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_recon_settlement ON "
            "razorpay_settlement_recon(tenant_id, settlement_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_recon_payment ON "
            "razorpay_settlement_recon(tenant_id, payment_id)"
        )
    except Exception:
        pass
    # 0009 — run POST idempotency keys (safe client retries)
    try:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS reconciliation_run_idempotency ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, "
            " tenant_id TEXT NOT NULL, "
            " idempotency_key TEXT NOT NULL, "
            " run_id TEXT NOT NULL, "
            " created_at TEXT NOT NULL DEFAULT (datetime('now')), "
            " UNIQUE (tenant_id, idempotency_key)"
            ")"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_idem_run ON "
            "reconciliation_run_idempotency(run_id)"
        )
    except Exception:
        pass
    for table, column, col_def in migrations:
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        except Exception:
            pass
    await db.commit()


async def init_db():
    """Ensure database tables exist and default tenant is present.

    PostgreSQL: Tables are managed by Alembic migrations.
    init_db() only ensures the default tenant exists.
    For fresh databases, run: alembic upgrade head

    SQLite: Creates tables directly (no Alembic needed for dev).
    """
    if USE_POSTGRES:
        pool = await _get_pg_pool()
        async with pool.acquire() as conn:
            # Verify tables exist (created by Alembic)
            result = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'tenants')"
            )
            if not result:
                logger.warning(
                    "PostgreSQL tables not found. Run 'alembic upgrade head' to create schema."
                )
            # Ensure default tenant
            await conn.execute(
                "INSERT INTO tenants (tenant_id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                "default", "Default",
            )
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.executescript(SQLITE_SCHEMA)
            await db.execute(
                "INSERT OR IGNORE INTO tenants (tenant_id, name) VALUES (?, ?)",
                ("default", "Default"),
            )
            await db.commit()
            await _sqlite_migrate(db)


async def log_audit(
    tenant_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    user_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
    request_id: Optional[str] = None,
):
    """Append an immutable audit log entry."""
    details_json = json.dumps(details or {})

    if USE_POSTGRES:
        pool = await _get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO audit_log (tenant_id, user_id, action, entity_type, entity_id, "
                "details, ip_address, request_id) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                tenant_id, user_id, action, entity_type, entity_id,
                details_json, ip_address, request_id,
            )
    else:
        db = await aiosqlite.connect(DB_PATH, timeout=30)
        try:
            await db.execute(
                "INSERT INTO audit_log (tenant_id, user_id, action, entity_type, entity_id, "
                "details, ip_address, request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (tenant_id, user_id, action, entity_type, entity_id,
                 details_json, ip_address, request_id),
            )
            await db.commit()
        finally:
            await db.close()


async def check_db_health() -> bool:
    """Quick health check — verify database is reachable."""
    try:
        if USE_POSTGRES:
            pool = await _get_pg_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        else:
            db = await aiosqlite.connect(DB_PATH, timeout=5)
            await db.execute("SELECT 1")
            await db.close()
        return True
    except Exception as e:
        logger.error("Database health check failed: %s", e)
        return False
