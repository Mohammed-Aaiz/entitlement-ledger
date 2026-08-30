"""Add ai_analyzed column to evidence table.

The AI scenario pipeline needs a flag to track whether evidence has been
consumed by an AI analysis run. Previously the system used
linked_decision_ids = '[]' to infer "unprocessed", but Razorpay evidence
gets linked to a deterministic decision immediately, making it invisible
to the AI pipeline.

ai_analyzed = FALSE  → eligible for AI scenario analysis
ai_analyzed = TRUE   → already consumed by AI, must not be reprocessed
"""

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column(
        "evidence",
        sa.Column("ai_analyzed", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
    )


def downgrade() -> None:
    op.drop_column("evidence", "ai_analyzed")
