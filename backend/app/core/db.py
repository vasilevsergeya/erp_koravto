from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import config


class Base(DeclarativeBase):
    pass


engine = create_engine(
    config().database_url,
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=2,
    connect_args={"connect_timeout": 3, "options": "-c statement_timeout=10000"},
    hide_parameters=True,
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session
