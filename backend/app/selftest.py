"""Acceptance tests in a disposable database, never against production data."""

import os
import secrets
import subprocess
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from app.core.config import config

cfg = config()
dsn = cfg.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
database = "crm_verify_" + secrets.token_hex(8)
env = os.environ.copy()
parts = urlsplit(cfg.database_url)
env["DATABASE_URL"] = urlunsplit(parts._replace(path="/" + database))
redis_parts = urlsplit(cfg.redis_url)
env["REDIS_URL"] = urlunsplit(redis_parts._replace(path="/15"))
env["CRM_INTEGRATION_TEST"] = "1"
with psycopg.connect(dsn, autocommit=True) as connection:
    connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
try:
    subprocess.run(["python", "-m", "app.migrate"], env=env, check=True)
    result = subprocess.run(
        ["python", "-m", "unittest", "discover", "-s", "tests", "-v"], env=env, check=False
    )
    raise SystemExit(result.returncode)
finally:
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
        )
