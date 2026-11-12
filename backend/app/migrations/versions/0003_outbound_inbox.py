"""Durable outgoing messages and interactive inbox; additive migration."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "delivery_receipts",
        sa.Column("external_id", sa.String(100), primary_key=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "conversations", sa.Column("assignee_id", sa.String(36), sa.ForeignKey("users.id"))
    )
    op.add_column(
        "conversations",
        sa.Column("needs_human", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "conversations",
        sa.Column("ai_mode", sa.String(20), nullable=False, server_default="default"),
    )
    op.add_column(
        "conversations", sa.Column("order_numbers", JSONB(), nullable=False, server_default="[]")
    )
    op.add_column(
        "conversations", sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "conversations",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column("messages", sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id")))
    op.add_column(
        "messages", sa.Column("quoted_message_id", sa.String(36), sa.ForeignKey("messages.id"))
    )
    op.add_column(
        "messages", sa.Column("quoted_text", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "messages",
        sa.Column("is_private_note", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("messages", sa.Column("error_code", sa.String(100)))
    op.add_column(
        "messages", sa.Column("send_attempts", sa.Integer(), nullable=False, server_default="0")
    )
    for column in ("send_started_at", "next_attempt_at", "sent_at"):
        op.add_column("messages", sa.Column(column, sa.DateTime(timezone=True)))
    op.create_index("ix_messages_next_attempt_at", "messages", ["next_attempt_at"])
    op.create_index(
        "ix_messages_search",
        "messages",
        [sa.text("to_tsvector('simple', body)")],
        postgresql_using="gin",
    )
    op.add_column("attachments", sa.Column("uploaded_by", sa.String(36), sa.ForeignKey("users.id")))
    op.add_column(
        "attachments",
        sa.Column(
            "mime", sa.String(100), nullable=False, server_default="application/octet-stream"
        ),
    )


def downgrade():
    op.drop_table("delivery_receipts")
    op.drop_column("attachments", "mime")
    op.drop_column("attachments", "uploaded_by")
    op.drop_index("ix_messages_search", "messages")
    op.drop_index("ix_messages_next_attempt_at", "messages")
    for column in (
        "author_id",
        "quoted_message_id",
        "quoted_text",
        "is_private_note",
        "error_code",
        "send_attempts",
        "send_started_at",
        "next_attempt_at",
        "sent_at",
    ):
        op.drop_column("messages", column)
    for column in (
        "assignee_id",
        "needs_human",
        "ai_mode",
        "order_numbers",
        "unread_count",
        "created_at",
    ):
        op.drop_column("conversations", column)
