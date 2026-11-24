"""Staff-only revisions of the persistent customer note."""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE TABLE customer_profile_notes (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        customer_id varchar(36) NOT NULL REFERENCES customers(id),
        author_id varchar(36) REFERENCES users(id),
        body varchar(2000) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute(
        "CREATE INDEX ix_customer_profile_notes_latest ON customer_profile_notes(customer_id,id DESC)"
    )


def downgrade():
    op.drop_table("customer_profile_notes")
