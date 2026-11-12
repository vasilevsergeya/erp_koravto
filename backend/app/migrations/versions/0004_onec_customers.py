"""Cached 1C customer matches, separate from manually maintained chat data."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "conversations", sa.Column("onec_clients", JSONB(), nullable=False, server_default="[]")
    )
    for name, length in (("onec_phone", 100), ("onec_config", 64)):
        op.add_column(
            "conversations", sa.Column(name, sa.String(length), nullable=False, server_default="")
        )
    for name in ("onec_checked_at", "onec_attempted_at", "onec_next_at"):
        op.add_column("conversations", sa.Column(name, sa.DateTime(timezone=True)))
    op.add_column("conversations", sa.Column("onec_error", sa.String(40)))
    op.create_index("ix_conversations_onec_next_at", "conversations", ["onec_next_at"])


def downgrade():
    op.drop_index("ix_conversations_onec_next_at", "conversations")
    for name in (
        "onec_clients",
        "onec_phone",
        "onec_config",
        "onec_checked_at",
        "onec_attempted_at",
        "onec_next_at",
        "onec_error",
    ):
        op.drop_column("conversations", name)
