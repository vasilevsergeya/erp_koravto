"""Personal manager language and paired AI replies; preserve recorded manual choices."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE users ADD COLUMN manager_language VARCHAR(20) NOT NULL DEFAULT 'en'")
    op.execute(
        "ALTER TABLE customers ADD COLUMN incoming_translation_auto BOOLEAN NOT NULL DEFAULT TRUE"
    )
    op.execute("ALTER TABLE customers ALTER COLUMN translation_client_language SET DEFAULT 'auto'")
    # The old default en was indistinguishable from a manual en selection except in the audit.
    # Preserve every recorded choice and all non-default languages.
    op.execute("""UPDATE customers c SET translation_client_language='auto'
        WHERE translation_client_language='en' AND NOT EXISTS
        (SELECT 1 FROM customer_events e WHERE e.customer_id=c.id
         AND e.details->'fields' ? 'translation_client_language')""")
    op.execute("""UPDATE customers c SET incoming_translation_auto=FALSE
        WHERE translate_incoming OR EXISTS
        (SELECT 1 FROM customer_events e WHERE e.customer_id=c.id
         AND e.details->'fields' ? 'translate_incoming')""")
    op.execute("""ALTER TABLE ai_drafts
        ADD COLUMN manager_language VARCHAR(20) NOT NULL DEFAULT 'en',
        ADD COLUMN manager_versions JSONB NOT NULL DEFAULT '{}',
        ADD COLUMN final_manager_text TEXT NOT NULL DEFAULT '',
        ADD COLUMN final_language VARCHAR(20) NOT NULL DEFAULT '',
        ADD COLUMN decision_input JSONB NOT NULL DEFAULT '{}'""")
    op.execute("""ALTER TABLE approved_examples
        ADD COLUMN manager_reply TEXT NOT NULL DEFAULT '',
        ADD COLUMN manager_language VARCHAR(20) NOT NULL DEFAULT 'en'""")


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back data migrations")
