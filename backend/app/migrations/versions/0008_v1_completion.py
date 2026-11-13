"""Employee access, quick replies and structured AI review."""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE users ADD COLUMN access_version INTEGER NOT NULL DEFAULT 1")
    op.execute(
        "CREATE TABLE user_access_tokens (token_hash VARCHAR(64) PRIMARY KEY, user_id VARCHAR(36) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE, expires_at TIMESTAMPTZ NOT NULL)"
    )
    op.execute(
        "CREATE TABLE quick_replies (id VARCHAR(36) PRIMARY KEY, title VARCHAR(100) NOT NULL, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1)"
    )
    op.execute(
        "ALTER TABLE ai_drafts ADD COLUMN rejection_category VARCHAR(20) NOT NULL DEFAULT ''"
    )

    op.execute(
        "CREATE TABLE integration_health (name VARCHAR(40) PRIMARY KEY, details JSONB NOT NULL, checked_at TIMESTAMPTZ NOT NULL)"
    )
    op.execute("CREATE INDEX ix_messages_timeline ON messages(conversation_id, created_at, id)")

    op.execute(
        "CREATE INDEX ix_messages_response ON messages (conversation_id, (COALESCE(sent_at,created_at)), id) WHERE direction='out' AND NOT is_private_note AND author_type!='system' AND status IN ('sent','delivered','read')"
    )


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back data migrations")
