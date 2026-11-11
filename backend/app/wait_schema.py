import time

from sqlalchemy import text

from app.core.db import engine

for attempt in range(120):
    try:
        with engine.connect() as connection:
            if (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == "0002"
            ):
                break
    except Exception:
        pass
    time.sleep(1)
else:
    raise SystemExit("Database migrations did not become ready")
