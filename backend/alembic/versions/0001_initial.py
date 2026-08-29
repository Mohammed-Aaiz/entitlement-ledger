"""Initial schema — all EntitlementLedger tables.

Revision ID: 0001_initial
Revises: None
Create Date: 2026-08-28

Creates the complete EntitlementLedger schema for PostgreSQL:
  tenants, users, decisions, evidence, policies, scenarios,
  razorpay_events, razorpay_orders, razorpay_payments,
  razorpay_settlements, razorpay_sync_metadata, audit_log

All tables include proper foreign keys and indexes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all EntitlementLedger tables on PostgreSQL.

    Each CREATE TABLE + its indexes are executed as a single batch.
    Tables are ordered to satisfy foreign key dependencies.
    """
    conn = op.get_bind()

    # Helper to execute multiple statements in sequence
    def run(sql: str):
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                conn.execute(sa.text(stmt))

    # 1. Tenants (no FK dependencies)
    run("""
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id VARCHAR(128) PRIMARY KEY,
            name VARCHAR(256) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # 2. Users (depends on tenants)
    run("""
        CREATE TABLE IF NOT EXISTS users (
            user_id VARCHAR(128) PRIMARY KEY,
            email VARCHAR(256) UNIQUE NOT NULL,
            password_hash VARCHAR(256) NOT NULL,
            display_name VARCHAR(256) NOT NULL,
            role VARCHAR(64) NOT NULL DEFAULT 'analyst',
            tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
    run("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    run("CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id)")

    # 3. Policies (no FK dependencies, shared)
    run("""
        CREATE TABLE IF NOT EXISTS policies (
            policy_id VARCHAR(128) PRIMARY KEY,
            version VARCHAR(32) NOT NULL,
            clause_text TEXT NOT NULL,
            effective_date VARCHAR(32) NOT NULL,
            expiration_date VARCHAR(32),
            tenant_id VARCHAR(128)
        )
    """)

    # 4. Decisions (depends on tenants)
    run("""
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
            approved_at TIMESTAMPTZ NOT NULL,
            model_output JSONB NOT NULL DEFAULT '{}',
            prev_decision_hash VARCHAR(128) NOT NULL,
            decision_hash VARCHAR(128) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status VARCHAR(32) NOT NULL DEFAULT 'REVIEW_REQUIRED'
        )
    """)
    run("CREATE INDEX IF NOT EXISTS idx_decisions_tenant ON decisions(tenant_id)")
    run("CREATE INDEX IF NOT EXISTS idx_decisions_entity ON decisions(tenant_id, entity_id)")
    run("CREATE INDEX IF NOT EXISTS idx_decisions_hash ON decisions(decision_hash)")
    run("CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at)")
    run("CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status)")

    # 5. Evidence (depends on tenants)
    run("""
        CREATE TABLE IF NOT EXISTS evidence (
            evidence_id VARCHAR(128) PRIMARY KEY,
            tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
            source_type VARCHAR(64) NOT NULL,
            raw_content TEXT NOT NULL,
            extracted_facts JSONB NOT NULL DEFAULT '[]',
            linked_decision_ids JSONB NOT NULL DEFAULT '[]',
            content_hash VARCHAR(128) NOT NULL DEFAULT '',
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        )
    """)
    run("CREATE INDEX IF NOT EXISTS idx_evidence_tenant ON evidence(tenant_id)")
    run("CREATE INDEX IF NOT EXISTS idx_evidence_hash ON evidence(content_hash)")

    # 6. Scenarios (no FK dependencies, dev/test only)
    run("""
        CREATE TABLE IF NOT EXISTS scenarios (
            scenario_id VARCHAR(128) PRIMARY KEY,
            name VARCHAR(256) NOT NULL,
            description TEXT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending'
        )
    """)

    # 7. Razorpay events (depends on tenants)
    run("""
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
        )
    """)
    run("CREATE INDEX IF NOT EXISTS idx_rp_events_tenant ON razorpay_events(tenant_id)")
    run("CREATE INDEX IF NOT EXISTS idx_rp_events_hash ON razorpay_events(payload_hash)")
    run("CREATE INDEX IF NOT EXISTS idx_rp_events_source ON razorpay_events(source)")

    # 8. Razorpay orders (depends on tenants)
    run("""
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
        )
    """)
    run("CREATE INDEX IF NOT EXISTS idx_rp_orders_tenant ON razorpay_orders(tenant_id)")
    run("CREATE INDEX IF NOT EXISTS idx_rp_orders_entity ON razorpay_orders(entity_id)")
    run("CREATE INDEX IF NOT EXISTS idx_rp_orders_status ON razorpay_orders(status)")

    # 9. Razorpay payments (depends on tenants)
    run("""
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
        )
    """)
    run("CREATE INDEX IF NOT EXISTS idx_rp_payments_tenant ON razorpay_payments(tenant_id)")
    run("CREATE INDEX IF NOT EXISTS idx_rp_payments_order ON razorpay_payments(order_id)")
    run("CREATE INDEX IF NOT EXISTS idx_rp_payments_entity ON razorpay_payments(entity_id)")
    run("CREATE INDEX IF NOT EXISTS idx_rp_payments_status ON razorpay_payments(status)")

    # 10. Razorpay settlements (depends on tenants)
    run("""
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
        )
    """)
    run("CREATE INDEX IF NOT EXISTS idx_rp_settlements_tenant ON razorpay_settlements(tenant_id)")
    run("CREATE INDEX IF NOT EXISTS idx_rp_settlements_status ON razorpay_settlements(status)")

    # 11. Razorpay sync metadata (depends on tenants)
    run("""
        CREATE TABLE IF NOT EXISTS razorpay_sync_metadata (
            sync_id SERIAL PRIMARY KEY,
            tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
            sync_type VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL,
            records_synced INTEGER DEFAULT 0,
            records_failed INTEGER DEFAULT 0,
            error_message TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ
        )
    """)

    # 12. Audit log (no FK dependencies, append-only)
    run("""
        CREATE TABLE IF NOT EXISTS audit_log (
            log_id SERIAL PRIMARY KEY,
            tenant_id VARCHAR(128) NOT NULL,
            user_id VARCHAR(128),
            action VARCHAR(128) NOT NULL,
            entity_type VARCHAR(64) NOT NULL,
            entity_id VARCHAR(128) NOT NULL,
            details JSONB NOT NULL DEFAULT '{}',
            ip_address VARCHAR(64),
            request_id VARCHAR(128),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    run("CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_log(tenant_id)")
    run("CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id)")
    run("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at)")
    run("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")

    # 13. Default tenant
    run("INSERT INTO tenants (tenant_id, name) VALUES ('default', 'Default') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    """Drop all EntitlementLedger tables (order matters for FKs)."""
    tables = [
        "audit_log",
        "razorpay_sync_metadata",
        "razorpay_settlements",
        "razorpay_payments",
        "razorpay_orders",
        "razorpay_events",
        "scenarios",
        "evidence",
        "decisions",
        "policies",
        "users",
        "tenants",
    ]
    for table in tables:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
