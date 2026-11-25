"""Keep the original Wazzup time when a CRM send is matched to its phone echo."""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = depends_on = None


def upgrade():
    op.execute("ALTER TABLE messages ADD COLUMN provider_at timestamptz")
    # The worker treats an ISO timestamp without an offset as UTC.
    op.execute("SET LOCAL TIME ZONE 'UTC'")
    op.execute("""
        WITH originals AS (
            SELECT DISTINCT ON (m.id) m.id, item->>'dateTime' AS stamp
            FROM webhook_events e
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(e.payload->'messages') = 'array'
                     THEN e.payload->'messages' ELSE '[]'::jsonb END
            ) WITH ORDINALITY AS items(item, position)
            JOIN messages m ON m.channel_id = item->>'channelId'
                           AND m.external_id = item->>'messageId'
            JOIN conversations c ON c.id = m.conversation_id AND c.chat_id = item->>'chatId'
            WHERE m.direction = 'out'
              AND (item->>'isEdited') IS DISTINCT FROM 'true'
              AND (item->>'isDeleted') IS DISTINCT FROM 'true'
              AND item->>'dateTime' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:'
              AND pg_input_is_valid(item->>'dateTime', 'timestamp with time zone')
            ORDER BY m.id, e.created_at, e.id, position
        )
        UPDATE messages m SET provider_at = originals.stamp::timestamptz
        FROM originals WHERE m.id = originals.id
    """)


def downgrade():
    op.drop_column("messages", "provider_at")
