"""Tier analysis on cases + Razorpay settlement recon table.

Adds:
- reconciliation_cases.tier_analysis: JSONB snapshot of the Tier 1-7 findings,
  tiers_applied, and the typed relationship/evidence graph for each case.
- razorpay_settlement_recon: deterministic settlement↔payment linkage data
  fetched from Razorpay GET /settlements/{id}/recon (used for Tier 3 linkage
  and Tier 4 fee/tax evidence).

The Tier 1-7 engine and relationship graph are deterministic modules; this
migration only persists their output so the Finance Control Room can expose
the tier breakdown from real case data.
"""

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column(
        "reconciliation_cases",
        sa.Column(
            "tier_analysis",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_table(
        "razorpay_settlement_recon",
        sa.Column("recon_id", sa.String(192), primary_key=True),
        sa.Column("tenant_id", sa.String(128), sa.ForeignKey("tenants.tenant_id"), nullable=False),
        sa.Column("settlement_id", sa.String(128), nullable=False),
        sa.Column("payment_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("order_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("fee", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tax", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("recon_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_recon_settlement", "razorpay_settlement_recon", ["tenant_id", "settlement_id"])
    op.create_index("idx_recon_payment", "razorpay_settlement_recon", ["tenant_id", "payment_id"])


def downgrade() -> None:
    op.drop_table("razorpay_settlement_recon")
    op.drop_column("reconciliation_cases", "tier_analysis")
