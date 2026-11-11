"""Use password-only authentication and remove retired TOTP enrollment secrets.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        sa.text("UPDATE users SET totp_secret = NULL, totp_enabled = FALSE, totp_counter = -1")
    )
    op.execute(sa.text("UPDATE sessions SET pending_totp_secret = NULL"))


def downgrade():
    # Erased enrollment secrets cannot be restored. Old code requires fresh enrollment.
    pass
