#!/usr/bin/env python3
"""Initialize infrastructure secrets locally; never print their values."""
import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
env_path = root / "deploy" / ".env"
if env_path.exists():
    raise SystemExit(".env already exists. Refusing to replace secrets.")

os.umask(0o077)
for name in ("files", "logs/app", "logs/caddy", "bootstrap", "secrets", "caddy", "caddy-config", "postgres", "redis"):
    (root / "data" / name).mkdir(parents=True, exist_ok=True)
(root / "backups").mkdir(exist_ok=True)
if os.geteuid() == 0:
    for name in ("files", "logs/app", "bootstrap"):
        os.chown(root / "data" / name, 10001, 10001)


def random_hex():
    return subprocess.check_output(["openssl", "rand", "-hex", "32"], text=True).strip()


password = random_hex()
secret_path = root / "data/secrets/postgres-password"
with os.fdopen(os.open(secret_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as output:
    output.write(password + "\n")
template = (root / "deploy/.env.example").read_text()
template = template.replace("REPLACE_ON_SERVER", password).replace("REPLACE_WITH_OPENSSL_RAND_HEX_32", random_hex(), 1)
template = template.replace("REPLACE_WITH_OPENSSL_RAND_HEX_32", random_hex(), 1)
with os.fdopen(os.open(env_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as output:
    output.write(template)
print("Infrastructure configuration generated; .env and database password mode 600")
