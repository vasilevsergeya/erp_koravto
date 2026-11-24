"""Customer trading type, separate from the marketing segment."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "customers",
        sa.Column("customer_type", sa.String(8), nullable=False, server_default="retail"),
    )
    op.create_check_constraint(
        "ck_customer_type", "customers", "customer_type IN ('retail','partner')"
    )


def downgrade():
    op.drop_column("customers", "customer_type")
