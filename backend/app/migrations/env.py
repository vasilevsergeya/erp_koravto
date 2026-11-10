from alembic import context

from app import models  # noqa: F401
from app.core.db import Base

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
with context.begin_transaction():
    context.run_migrations()
