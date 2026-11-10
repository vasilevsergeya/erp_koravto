#!/usr/bin/env python3
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path("/backups")
FILES = Path("/files")


def pg_environment():
    parsed = urlsplit(os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1))
    env = os.environ.copy()
    env.update({"PGHOST": parsed.hostname or "postgres", "PGPORT": str(parsed.port or 5432),
                "PGUSER": unquote(parsed.username or "crm"), "PGPASSWORD": unquote(parsed.password or ""),
                "PGDATABASE": unquote(parsed.path.lstrip("/")), "PGCONNECT_TIMEOUT": "10"})
    return env


def run(args, env):
    result = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name}_failed")
    return result.stdout


def digest(path):
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def log(event, **fields):
    print(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "event": event, **fields}), flush=True)


def backup():
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(ROOT / ".backup.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        timestamp = datetime.now(ZoneInfo(os.getenv("TZ", "Asia/Seoul"))).strftime("%Y%m%d-%H%M%S")
        name = "crm-" + timestamp
        work = ROOT / (".partial-" + name)
        work.mkdir()
        try:
            env = pg_environment()
            dump = work / "database.dump"
            run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(dump)], env)
            run(["pg_restore", "--list", str(dump)], env)
            archive = work / "attachments.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                # Files are immutable UUID objects. Database snapshot precedes files snapshot.
                for file in FILES.iterdir():
                    if file.is_file() and not file.is_symlink():
                        output.add(file, arcname=file.name, recursive=False)
            metadata = {"created_at": datetime.now(timezone.utc).isoformat(),
                        "database": env["PGDATABASE"], "files_backend": "local-volume",
                        "master_key_included": False,
                        "sha256": {file.name: digest(file) for file in (dump, archive)}}
            (work / "manifest.json").write_text(json.dumps(metadata, indent=2))
            work.rename(ROOT / name)
            bucket = os.getenv("BACKUP_S3_BUCKET")
            if bucket:
                import boto3
                client = boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
                                      region_name=os.getenv("S3_REGION") or None,
                                      aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID") or None,
                                      aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY") or None)
                for file in (ROOT / name).iterdir():
                    client.upload_file(str(file), bucket, "crm/" + name + "/" + file.name)
            limit = time.time() - 30 * 86400
            for path in ROOT.glob("crm-*"):
                if re.fullmatch(r"crm-\d{8}-\d{6}", path.name) and path.is_dir() and not path.is_symlink() and path.stat().st_mtime < limit:
                    shutil.rmtree(path)
            (ROOT / "status.json").write_text(json.dumps({"last_success": metadata["created_at"], "snapshot": name}))
            log("backup.complete", snapshot=name, bytes=dump.stat().st_size if dump.exists() else (ROOT / name / "database.dump").stat().st_size)
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            raise


def restore(snapshot, database):
    if not re.fullmatch(r"crm_restore_[a-zA-Z0-9_]{1,40}", database):
        raise ValueError("Target must be a NEW database named crm_restore_<name>")
    snapshot = Path(snapshot).resolve(strict=True)
    manifest = json.loads((snapshot / "manifest.json").read_text())
    for name in ("database.dump", "attachments.tar.gz"):
        if digest(snapshot / name) != manifest["sha256"][name]:
            raise ValueError("Backup checksum mismatch")
    env = pg_environment()
    # createdb refuses an existing target. Production databases are never dropped here.
    run(["createdb", database], env)
    try:
        run(["pg_restore", "--exit-on-error", "--single-transaction", "--no-owner", "--no-acl", "--dbname", database, str(snapshot / "database.dump")], env)
        log("restore.complete", database=database)
    except Exception:
        log("restore.failed", database=database, action="Inspect the new target; source database was not changed")
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["once", "schedule", "restore"])
    parser.add_argument("snapshot", nargs="?")
    parser.add_argument("--database")
    args = parser.parse_args()
    if args.action == "once":
        backup()
    elif args.action == "restore":
        if not args.snapshot or not args.database:
            parser.error("restore requires SNAPSHOT --database crm_restore_NAME")
        restore(args.snapshot, args.database)
    else:
        zone = ZoneInfo(os.getenv("TZ", "Asia/Seoul"))
        while True:
            current = datetime.now(zone)
            target = current.replace(hour=3, minute=0, second=0, microsecond=0)
            if target <= current:
                target += timedelta(days=1)
            log("backup.scheduled", next_run=target.isoformat())
            while datetime.now(zone) < target:
                time.sleep(min(60, max(0, (target - datetime.now(zone)).total_seconds())))
            for attempt in range(3):
                try:
                    backup()
                    break
                except Exception as error:
                    log("backup.failed", error_type=type(error).__name__, attempt=attempt + 1)
                    time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        log("backup.command_failed", error_type=type(error).__name__)
        raise SystemExit(1) from None
