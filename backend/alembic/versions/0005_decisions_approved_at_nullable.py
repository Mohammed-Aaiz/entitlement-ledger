"""Make decisions.approved_at nullable.

AI-generated REVIEW_REQUIRED decisions have not been approved yet and
must not receive a fake approval timestamp.  The column was previously
NOT NULL, forcing the pipeline to insert an empty string which
PostgreSQL rejects (expects TIMESTAMPTZ, got text).

Razorpay and human-approved decisions continue to populate this field
with a real timestamp.
"""

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.alter_column(
        "decisions",
        "approved_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=True,
    )


def downgrade() -> None:
    # Before making NOT NULL again, set any NULLs to a sentinel so the
    # downgrade doesn't fail on existing rows.
    op.execute(
        "UPDATE decisions SET approved_at = '1970-01-01T00:00:00+00:00' "
        "WHERE approved_at IS NULL"
    )
    op.alter_column(
        "decisions",
        "approved_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
    )
