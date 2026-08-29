"""Add razorpay_account_mappings table.

Maps Razorpay account_id (from webhook payloads) to EntitlementLedger tenant_id.
This enables secure tenant resolution for incoming webhooks.

Revision ID: 0002
Revises: 0001_initial
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_razorpay_account_mappings"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS razorpay_account_mappings (
            account_id VARCHAR(128) PRIMARY KEY,
            tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(tenant_id),
            webhook_secret VARCHAR(256),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_ram_tenant ON razorpay_account_mappings(tenant_id)"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS razorpay_account_mappings CASCADE"))
