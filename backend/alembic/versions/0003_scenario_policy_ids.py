"""Add policy_ids column to scenarios table.

Scenarios need to know which policies they apply. Previously this mapping
was hardcoded in seed_data.py. Now it lives in the database so production
environments (where seed data is not loaded) can run scenarios.
"""

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import sqlalchemy as sa

    # PostgreSQL
    op.add_column(
        "scenarios",
        sa.Column("policy_ids", sa.Text, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("scenarios", "policy_ids")
