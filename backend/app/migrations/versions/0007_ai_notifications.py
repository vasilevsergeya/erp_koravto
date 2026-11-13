"""AI workflow and notification delivery: immutable additive schema."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE users ADD COLUMN notification_prefs JSONB NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE users ADD COLUMN telegram_chat_id VARCHAR(30)")
    op.execute(
        "\nCREATE TABLE ai_drafts (\n\tid VARCHAR(36) NOT NULL, \n\tconversation_id VARCHAR(36) NOT NULL, \n\tinbound_id VARCHAR(36) NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tdecision VARCHAR(20) NOT NULL, \n\tintent VARCHAR(40) NOT NULL, \n\tlanguage VARCHAR(20) NOT NULL, \n\tentities JSONB NOT NULL, \n\ttool_calls JSONB NOT NULL, \n\tpolicy JSONB NOT NULL, \n\tbody TEXT NOT NULL, \n\tfinal_text TEXT NOT NULL, \n\treason TEXT NOT NULL, \n\tmode VARCHAR(20) NOT NULL, \n\tsource_hash VARCHAR(64) NOT NULL, \n\tprompt_id VARCHAR(36), \n\tmessage_id VARCHAR(36), \n\tdecided_by VARCHAR(36), \n\tdecided_at TIMESTAMP WITH TIME ZONE, \n\tauto_at TIMESTAMP WITH TIME ZONE, \n\tlease_until TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_ai_generation UNIQUE (inbound_id, generation), \n\tFOREIGN KEY(conversation_id) REFERENCES conversations (id), \n\tFOREIGN KEY(inbound_id) REFERENCES messages (id), \n\tFOREIGN KEY(message_id) REFERENCES messages (id), \n\tFOREIGN KEY(decided_by) REFERENCES users (id)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_ai_drafts_state ON ai_drafts (state)")
    op.execute("CREATE INDEX ix_ai_drafts_inbound_id ON ai_drafts (inbound_id)")
    op.execute("CREATE INDEX ix_ai_drafts_conversation_id ON ai_drafts (conversation_id)")
    op.execute("CREATE INDEX ix_ai_drafts_auto_at ON ai_drafts (auto_at)")
    op.execute(
        "\nCREATE TABLE ai_prompts (\n\tid VARCHAR(36) NOT NULL, \n\tbody TEXT NOT NULL, \n\tcreated_by VARCHAR(36), \n\tactive BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(created_by) REFERENCES users (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE ai_usage (\n\tid VARCHAR(36) NOT NULL, \n\tdraft_id VARCHAR(36), \n\tmodel VARCHAR(100) NOT NULL, \n\tpurpose VARCHAR(20) NOT NULL, \n\ttokens_in INTEGER NOT NULL, \n\ttokens_out INTEGER NOT NULL, \n\tcost_usd NUMERIC(14, 8) NOT NULL, \n\terror VARCHAR(80), \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(draft_id) REFERENCES ai_drafts (id)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_ai_usage_created_at ON ai_usage (created_at)")
    op.execute("CREATE INDEX ix_ai_usage_draft_id ON ai_usage (draft_id)")
    op.execute(
        "\nCREATE TABLE approved_examples (\n\tid VARCHAR(36) NOT NULL, \n\tdraft_id VARCHAR(36) NOT NULL, \n\tintent VARCHAR(40) NOT NULL, \n\tlanguage VARCHAR(20) NOT NULL, \n\tquestion TEXT NOT NULL, \n\treply TEXT NOT NULL, \n\tembedding vector(1536), \n\tembedding_model VARCHAR(100) NOT NULL, \n\tnext_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (draft_id), \n\tFOREIGN KEY(draft_id) REFERENCES ai_drafts (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE notification_deliveries (\n\tid VARCHAR(36) NOT NULL, \n\tnotification_id VARCHAR(36) NOT NULL, \n\tchannel VARCHAR(20) NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tattempts INTEGER NOT NULL, \n\terror VARCHAR(80), \n\tnext_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tdelivered_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_notification_channel UNIQUE (notification_id, channel), \n\tFOREIGN KEY(notification_id) REFERENCES notifications (id)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_notification_deliveries_state ON notification_deliveries (state)")
    op.execute(
        "CREATE INDEX ix_notification_deliveries_notification_id ON notification_deliveries (notification_id)"
    )
    op.execute("CREATE UNIQUE INDEX uq_ai_active_prompt ON ai_prompts (active) WHERE active")
    op.execute(
        "CREATE INDEX ix_examples_embedding ON approved_examples USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade():
    raise RuntimeError("Restore the saved release; AI history must not be dropped")
