"""Permanent customer numbers, including searchable aliases after a merge."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = depends_on = None


def upgrade():
    op.execute("LOCK TABLE customers IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""
        CREATE TABLE customer_numbers (
          number bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          customer_id varchar(36) NOT NULL REFERENCES customers(id)
        )
    """)
    op.execute("CREATE INDEX ix_customer_numbers_owner ON customer_numbers(customer_id, number)")
    op.execute("""
        INSERT INTO customer_numbers(customer_id)
        SELECT c.id FROM customers c
        LEFT JOIN (
            SELECT d.customer_id, min(CASE WHEN m.direction='out' THEN
                coalesce(m.sent_at,m.created_at) ELSE m.created_at END) AS first_at
            FROM conversations d JOIN messages m ON m.conversation_id=d.id
            WHERE NOT m.is_private_note AND m.author_type <> 'system'
            GROUP BY d.customer_id
        ) history ON history.customer_id=c.id
        WHERE c.merged_into_id IS NULL
        ORDER BY coalesce(history.first_at,c.created_at),c.created_at,c.id
    """)
    op.execute("""
        CREATE FUNCTION assign_customer_number() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN INSERT INTO customer_numbers(customer_id) VALUES (NEW.id); RETURN NEW; END $$
    """)
    op.execute("""CREATE TRIGGER assign_customer_number AFTER INSERT ON customers
                  FOR EACH ROW EXECUTE FUNCTION assign_customer_number()""")


def downgrade():
    op.execute("DROP TRIGGER assign_customer_number ON customers")
    op.execute("DROP FUNCTION assign_customer_number()")
    op.drop_table("customer_numbers")
