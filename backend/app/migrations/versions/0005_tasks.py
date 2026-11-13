"""Tasks, their immutable history and durable in-app notifications."""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "boards", sa.Column("archived", sa.Boolean(), nullable=False, server_default="false")
    )
    op.add_column(
        "conversations",
        sa.Column("task_updated", sa.Boolean(), nullable=False, server_default="false"),
    )
    boards = sa.table("boards", sa.column("id", sa.String()), sa.column("statuses", JSONB()))
    for board in op.get_bind().execute(sa.select(boards)).mappings():
        statuses = [dict(s, id=str(uuid.uuid4())) for s in board["statuses"]]
        op.get_bind().execute(
            boards.update().where(boards.c.id == board["id"]).values(statuses=statuses)
        )
    op.create_table(
        "task_types",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
    )
    types = sa.table("task_types", sa.column("id"), sa.column("name"), sa.column("position"))
    op.bulk_insert(
        types,
        [
            {"id": str(uuid.uuid4()), "name": name, "position": i}
            for i, name in enumerate(
                ["Выкуп", "Заказ поставщику", "Возврат", "Хозяйство", "Прочее"]
            )
        ],
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("number", sa.BigInteger(), sa.Identity(start=1001), nullable=False, unique=True),
        sa.Column("client_id", sa.String(36), nullable=False, unique=True),
        sa.Column("board_id", sa.String(36), sa.ForeignKey("boards.id"), nullable=False),
        sa.Column("status_id", sa.String(36), nullable=False),
        sa.Column("type_id", sa.String(36), sa.ForeignKey("task_types.id"), nullable=False),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("priority", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assignee_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id")),
        sa.Column("order_no", sa.String(100), nullable=False, server_default=""),
        sa.Column("part_no", sa.String(100), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer()),
        sa.Column("labels", JSONB(), nullable=False, server_default="[]"),
        sa.Column("watchers", JSONB(), nullable=False, server_default="[]"),
        sa.Column("attachment_ids", JSONB(), nullable=False, server_default="[]"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_task_quantity"),
        sa.CheckConstraint("priority IN ('low','normal','high','urgent')", name="ck_task_priority"),
    )
    for field in [
        "board_id",
        "status_id",
        "author_id",
        "assignee_id",
        "due_at",
        "conversation_id",
        "created_at",
    ]:
        op.create_index("ix_tasks_" + field, "tasks", [field])
    op.create_table(
        "task_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("mentions", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "task_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("changes", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    for table in ["task_comments", "task_history"]:
        op.create_index("ix_" + table + "_task_id", table, ["task_id"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("dedupe_key", sa.String(180), unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("read_at", sa.DateTime(timezone=True)),
    )
    for field in ["user_id", "created_at"]:
        op.create_index("ix_notifications_" + field, "notifications", [field])


def downgrade():
    for table in ["notifications", "task_history", "task_comments", "tasks", "task_types"]:
        op.drop_table(table)
    op.drop_column("conversations", "task_updated")
    op.drop_column("boards", "archived")
