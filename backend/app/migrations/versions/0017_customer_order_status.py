"""Verified customer order totals and explicit counterparty links."""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = depends_on = None


def upgrade():
    op.execute("""ALTER TABLE customers ADD COLUMN order_count integer,
                  ADD COLUMN order_config varchar(64)""")
    op.execute("""CREATE TABLE customer_onec_links (
      customer_id varchar(36) NOT NULL REFERENCES customers(id),
      config varchar(64) NOT NULL, account_id varchar(128) NOT NULL,
      lookup_phone varchar(20) NOT NULL, excluded boolean NOT NULL DEFAULT false,
      snapshot jsonb, next_check timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(customer_id,config,account_id))""")
    op.execute(
        "CREATE INDEX ix_customer_onec_due ON customer_onec_links(next_check) WHERE NOT excluded"
    )


def downgrade():
    op.drop_table("customer_onec_links")
    op.drop_column("customers", "order_config")
    op.drop_column("customers", "order_count")
