"""Private, bounded Wazzup profile image cache."""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE TABLE customer_avatars (
        customer_id varchar(36) PRIMARY KEY REFERENCES customers(id),
        source text NOT NULL, photo bytea, mime varchar(24), updated_at timestamptz,
        next_attempt_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("CREATE INDEX ix_customer_avatar_due ON customer_avatars(next_attempt_at)")
    op.execute("""
        INSERT INTO customer_avatars(customer_id,source)
        SELECT DISTINCT ON (c.customer_id) c.customer_id, m#>>'{contact,avatarUri}'
        FROM webhook_events e CROSS JOIN LATERAL jsonb_array_elements(
          CASE WHEN jsonb_typeof(e.payload->'messages')='array' THEN e.payload->'messages' ELSE '[]'::jsonb END) m
        JOIN conversations c ON c.channel_id=m->>'channelId' AND c.chat_id=m->>'chatId'
        WHERE c.customer_id IS NOT NULL AND m->>'status'='inbound' AND
              m#>>'{contact,avatarUri}' LIKE 'https://%' AND length(m#>>'{contact,avatarUri}')<=4096
        ORDER BY c.customer_id,e.created_at DESC,e.id DESC
    """)


def downgrade():
    op.drop_table("customer_avatars")
