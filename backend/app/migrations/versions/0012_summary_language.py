"""Remember each manager's preferred conversation summary language."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("summary_language", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("users", "summary_language")
