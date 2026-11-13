"""Unified customer profiles and deterministic phone backfill; no message rewrites."""

import hashlib
import json
import uuid

import phonenumbers
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    def timestamp():
        return sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        )

    op.create_table(
        "customers",
        sa.Column("id", sa.String(36), primary_key=True),
        *[
            sa.Column(k, sa.String(n), nullable=False, server_default="")
            for k, n in [("name", 200), ("language", 20), ("segment", 20), ("country", 2)]
        ],
        sa.Column("name_manual", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("order_numbers", JSONB(), nullable=False, server_default="[]"),
        sa.Column("default_owner_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("merged_into_id", sa.String(36), sa.ForeignKey("customers.id")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        timestamp(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_customers_merged_into_id", "customers", ["merged_into_id"])
    op.create_index("ix_customers_updated_at", "customers", ["updated_at"])
    op.create_table(
        "customer_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("value", sa.String(500), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
        timestamp(),
        sa.UniqueConstraint("type", "value", name="uq_customer_identity"),
    )
    op.create_table(
        "customer_notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), nullable=False, unique=True),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        timestamp(),
    )
    op.create_table(
        "customer_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("details", JSONB(), nullable=False, server_default="{}"),
        timestamp(),
    )
    for table in ["customer_identities", "customer_notes", "customer_events"]:
        op.create_index("ix_" + table + "_customer_id", table, ["customer_id"])
    for table in ["conversations", "tasks"]:
        op.add_column(table, sa.Column("customer_id", sa.String(36), sa.ForeignKey("customers.id")))
        op.create_index("ix_" + table + "_customer_id", table, ["customer_id"])
    op.add_column(
        "conversations",
        sa.Column("customer_identity_id", sa.String(36), sa.ForeignKey("customer_identities.id")),
    )
    db = op.get_bind()
    metadata = sa.MetaData()
    customers = sa.Table("customers", metadata, autoload_with=db)
    identities = sa.Table("customer_identities", metadata, autoload_with=db)
    conversations = sa.Table("conversations", metadata, autoload_with=db)
    known = {}
    for c in db.execute(
        sa.select(conversations).order_by(conversations.c.created_at, conversations.c.id)
    ).mappings():
        if c["chat_type"] != "whatsapp":
            continue
        phone, country = None, ""
        try:
            parsed = phonenumbers.parse(c["phone"] or "+" + c["chat_id"], None)
            if phonenumbers.is_valid_number(parsed):
                phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
                country = phonenumbers.region_code_for_number(parsed) or ""
                if country == "001":
                    country = ""
        except phonenumbers.NumberParseException:
            pass
        scoped = hashlib.sha256(
            json.dumps(
                [c["channel_id"], c["chat_type"], c["chat_id"]],
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        key = ("phone", phone) if phone else ("wazzup_chat", scoped)
        if key not in known:
            cid, iid = str(uuid.uuid4()), str(uuid.uuid4())
            db.execute(
                customers.insert().values(
                    id=cid,
                    name=c["name"],
                    country=country,
                    default_owner_id=c["assignee_id"],
                    created_at=c["created_at"],
                )
            )
            db.execute(
                identities.insert().values(
                    id=iid,
                    customer_id=cid,
                    type=key[0],
                    value=key[1],
                    source="backfill",
                    verified=bool(phone),
                )
            )
            known[key] = (cid, iid)
        cid, iid = known[key]
        if phone:
            db.execute(
                identities.insert().values(
                    id=str(uuid.uuid4()),
                    customer_id=cid,
                    type="wazzup_chat",
                    value=scoped,
                    source="backfill",
                    verified=True,
                )
            )
        db.execute(
            conversations.update()
            .where(conversations.c.id == c["id"])
            .values(customer_id=cid, customer_identity_id=iid)
        )
    db.execute(
        sa.text(
            "UPDATE tasks t SET customer_id=c.customer_id "
            "FROM conversations c WHERE t.conversation_id=c.id"
        )
    )


def downgrade():
    op.drop_column("tasks", "customer_id")
    op.drop_column("conversations", "customer_identity_id")
    op.drop_column("conversations", "customer_id")
    for table in ["customer_events", "customer_notes", "customer_identities", "customers"]:
        op.drop_table(table)
