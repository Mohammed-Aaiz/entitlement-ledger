"""Add first-class AI investigation metadata to reconciliation_cases.

First-class persistence for the controlled AI investigation pipeline:

- ai_invoked: whether the AI provider was GENUINELY invoked for this case
  (distinct from ai_status, which is "not_needed" for gated-out cases).
- ai_trigger_reason: the deterministic gate's reason for invoking or
  skipping AI (demand-driven policy).
- ai_tool_calls: how many read-only investigator tool calls were executed
  during the bounded investigation loop.

These replace the previous JSON-embedded "_gate" metadata hack so the
metadata is queryable and unambiguous.
"""

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column(
        "reconciliation_cases",
        sa.Column("ai_invoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "reconciliation_cases",
        sa.Column("ai_trigger_reason", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "reconciliation_cases",
        sa.Column("ai_tool_calls", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_cases", "ai_tool_calls")
    op.drop_column("reconciliation_cases", "ai_trigger_reason")
    op.drop_column("reconciliation_cases", "ai_invoked")