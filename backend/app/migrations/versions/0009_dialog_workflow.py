"""Reply-based unread state, message actions and durable bilingual messages."""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE messages ADD COLUMN reply_acknowledged BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute(
        "UPDATE messages SET reply_acknowledged=TRUE WHERE direction='out' AND status IN ('sent','delivered','read')"
    )
    op.execute(
        "ALTER TABLE conversations ADD COLUMN inbound_seq INTEGER NOT NULL DEFAULT 0, ADD COLUMN answered_seq INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE messages ADD COLUMN version INTEGER NOT NULL DEFAULT 1, ADD COLUMN inbound_seq INTEGER, ADD COLUMN reply_through_seq INTEGER, ADD COLUMN task_id VARCHAR(36) REFERENCES tasks(id), ADD COLUMN delivery_body TEXT"
    )
    op.execute(
        "ALTER TABLE customers ADD COLUMN translate_incoming BOOLEAN NOT NULL DEFAULT FALSE, ADD COLUMN translate_outgoing BOOLEAN NOT NULL DEFAULT FALSE, ADD COLUMN translation_client_language VARCHAR(20) NOT NULL DEFAULT 'en', ADD COLUMN translation_manager_language VARCHAR(20) NOT NULL DEFAULT 'ru'"
    )
    op.execute(
        "CREATE TABLE message_hidden (user_id VARCHAR(36) NOT NULL REFERENCES users(id), message_id VARCHAR(36) NOT NULL REFERENCES messages(id) ON DELETE CASCADE, PRIMARY KEY(user_id,message_id))"
    )
    op.execute(
        "CREATE TABLE message_actions (id VARCHAR(36) PRIMARY KEY, message_id VARCHAR(36) NOT NULL REFERENCES messages(id), user_id VARCHAR(36) NOT NULL REFERENCES users(id), kind VARCHAR(10) NOT NULL, body TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL, state VARCHAR(20) NOT NULL DEFAULT 'queued', error VARCHAR(100), created_at TIMESTAMPTZ NOT NULL, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_message_active_action ON message_actions(message_id) WHERE state IN ('queued','processing','uncertain')"
    )
    op.execute(
        "CREATE TABLE message_translations (id VARCHAR(36) PRIMARY KEY, message_id VARCHAR(36) NOT NULL REFERENCES messages(id) ON DELETE CASCADE, revision INTEGER NOT NULL, source_language VARCHAR(20) NOT NULL, target_language VARCHAR(20) NOT NULL, original TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', state VARCHAR(20) NOT NULL DEFAULT 'pending', error VARCHAR(100), attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, UNIQUE(message_id,revision,source_language,target_language))"
    )
    op.execute("CREATE INDEX ix_translations_message ON message_translations(message_id)")
    op.execute(
        "ALTER TABLE messages ADD COLUMN delivery_source_language VARCHAR(20) NOT NULL DEFAULT '', ADD COLUMN delivery_target_language VARCHAR(20) NOT NULL DEFAULT ''"
    )
    op.execute(
        "WITH numbered AS (SELECT id,row_number() OVER(PARTITION BY conversation_id ORDER BY created_at,id) AS seq FROM messages WHERE direction='in' AND NOT is_private_note) UPDATE messages m SET inbound_seq=n.seq FROM numbered n WHERE m.id=n.id"
    )
    op.execute(
        "UPDATE conversations c SET inbound_seq=COALESCE((SELECT max(inbound_seq) FROM messages WHERE conversation_id=c.id),0), answered_seq=COALESCE((SELECT max(i.inbound_seq) FROM messages i WHERE i.conversation_id=c.id AND i.created_at <= (SELECT max(o.created_at) FROM messages o WHERE o.conversation_id=c.id AND o.direction='out' AND NOT o.is_private_note AND o.status IN ('sent','delivered','read'))),0)"
    )
    op.execute(
        "UPDATE conversations c SET unread_count=(SELECT count(*) FROM messages m WHERE m.conversation_id=c.id AND m.inbound_seq>c.answered_seq AND NOT m.deleted)"
    )
    op.execute(
        "UPDATE conversations SET state=CASE WHEN unread_count>0 THEN 'waiting_us' ELSE 'waiting_customer' END WHERE state!='closed' OR unread_count>0"
    )
    op.execute(
        "UPDATE messages m SET task_id=t.id FROM tasks t WHERE m.external_id LIKE 'task:%' AND m.conversation_id=t.conversation_id AND m.body LIKE 'Задача T-' || t.number::text || ' %'"
    )


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back data migrations")
