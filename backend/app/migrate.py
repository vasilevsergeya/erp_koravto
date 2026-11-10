from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.core.db import engine


def main():
    config = Config("alembic.ini")
    with engine.connect() as connection:
        # Serialize migrations even if multiple API containers start together.
        connection.execute(text("SET statement_timeout = '120s'"))
        connection.execute(text("SELECT pg_advisory_lock(1182347001)"))
        connection.commit()
        try:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(1182347001)"))
            connection.commit()


if __name__ == "__main__":
    main()
