"""Add reconciliation domain tables.

Creates the normalized financial record store, per-payment reconciliation
cases, and batch reconciliation runs used by the finance controller:

- reconciliation_records: normalized payments/refunds/settlements/fee-tax/
  adjustments in integer paise, idempotent per (tenant, source, external_id).
- reconciliation_cases: one row per payment with the deterministic
  outcome (expected/actual/variance, classification, exception codes,
  AI interpretation metadata, calculation trace, ledger decision link).
- reconciliation_runs: aggregate metrics computed from real cases
  (match rate, accuracy, false auto-resolve, latency, duplicates,
  audit completeness).
"""

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.create_table(
        "reconciliation_records",
        sa.Column("record_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), sa.ForeignKey("tenants.tenant_id"), nullable=False),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(64), nullable=False, server_default="unknown"),
        sa.Column("payment_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("order_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("fee_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("adjustment_sign", sa.String(16), nullable=False, server_default=""),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="batch"),
        sa.Column("raw_evidence_ref", sa.String(256), nullable=False, server_default=""),
        sa.Column("payload_hash", sa.String(128), nullable=False, server_default=""),
        sa.Column("extra", sa.dialects.postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_rec_records_tenant", "reconciliation_records", ["tenant_id"])
    op.create_index("idx_rec_records_payment", "reconciliation_records", ["tenant_id", "payment_id"])
    op.create_index("idx_rec_records_type", "reconciliation_records", ["record_type"])

    op.create_table(
        "reconciliation_cases",
        sa.Column("case_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), sa.ForeignKey("tenants.tenant_id"), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("payment_id", sa.String(128), nullable=False),
        sa.Column("related_record_ids", sa.dialects.postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("expected_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("actual_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("variance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("classification", sa.String(32), nullable=False, server_default="REVIEW_REQUIRED"),
        sa.Column("exception_codes", sa.dialects.postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("exceptions", sa.dialects.postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("ai_status", sa.String(32), nullable=False, server_default="not_needed"),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("ai_interpretation", sa.dialects.postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ai_technical_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("calculation_trace", sa.dialects.postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("match_info", sa.dialects.postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("decision_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_rec_cases_tenant", "reconciliation_cases", ["tenant_id"])
    op.create_index("idx_rec_cases_run", "reconciliation_cases", ["tenant_id", "run_id"])
    op.create_index("idx_rec_cases_class", "reconciliation_cases", ["classification"])
    op.create_index("idx_rec_cases_payment", "reconciliation_cases", ["tenant_id", "payment_id"])

    op.create_table(
        "reconciliation_runs",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), sa.ForeignKey("tenants.tenant_id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("source", sa.String(32), nullable=False, server_default="batch"),
        sa.Column("total_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_required", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exceptions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("match_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("classification_accuracy", sa.Float(), nullable=True),
        sa.Column("calculation_accuracy", sa.Float(), nullable=True),
        sa.Column("false_auto_resolve", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("throughput_per_sec", sa.Float(), nullable=False, server_default="0"),
        sa.Column("p50_latency_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("p95_latency_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("duplicates_detected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("audit_completeness", sa.Float(), nullable=False, server_default="0"),
        sa.Column("errors", sa.dialects.postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_rec_runs_tenant", "reconciliation_runs", ["tenant_id"])
    op.create_index("idx_rec_runs_status", "reconciliation_runs", ["status"])


def downgrade() -> None:
    op.drop_table("reconciliation_runs")
    op.drop_table("reconciliation_cases")
    op.drop_table("reconciliation_records")