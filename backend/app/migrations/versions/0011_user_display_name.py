"""Add a personal display name for CRM users."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("display_name", sa.String(100), nullable=True))


def downgrade():
    op.drop_column("users", "display_name")
