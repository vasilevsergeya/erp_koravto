import argparse
import os
import secrets
import subprocess
from pathlib import Path

from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.core.settings import invalidate_settings, put_setting
from app.models import Board, LoginSession, Setting, User

BOARDS = {
    "Закупки": ["Новая", "В работе", "Заказано", "Получено", "Готово", "Отменено"],
    "Хозяйство": ["Новая", "В работе", "Готово"],
    "Клиенты": ["Новая", "В работе", "Ждём клиента", "Готово"],
}


def seed_boards(db):
    for order, (name, statuses) in enumerate(BOARDS.items()):
        if not db.scalar(select(Board).where(Board.name == name)):
            db.add(
                Board(
                    name=name,
                    position=order,
                    statuses=[
                        {
                            "name": status,
                            "position": position,
                            "terminal": status in ("Готово", "Отменено"),
                            "color": "green"
                            if status == "Готово"
                            else "gray"
                            if status == "Отменено"
                            else "blue",
                        }
                        for position, status in enumerate(statuses)
                    ],
                )
            )
    if not db.get(Setting, "wazzup.webhook_token"):
        put_setting(db, "wazzup.webhook_token", secrets.token_hex(32), True, None)
    db.commit()
    invalidate_settings()


def main():
    parser = argparse.ArgumentParser(description="CRM maintenance; secrets are never printed")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create-admin", "reset-admin"):
        command = commands.add_parser(name)
        command.add_argument("--email", required=True)
        command.add_argument(
            "--password-file", required=True, help="New file, mode 600; must not already exist"
        )
    commands.add_parser("seed-boards")
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.command == "seed-boards":
            seed_boards(db)
            print("Default boards and webhook token initialized (idempotent)")
            return
        email = args.email.strip().lower()
        if "@" not in email or len(email) > 254:
            raise SystemExit("Invalid email")
        user = db.scalar(select(User).where(User.email == email))
        if args.command == "create-admin" and user:
            raise SystemExit("User already exists; credentials were not changed")
        if args.command == "reset-admin" and not user:
            raise SystemExit("User not found")
        password = subprocess.check_output(["openssl", "rand", "-base64", "24"], text=True).strip()
        path = Path(args.password_file)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "w") as output:
                output.write(password + "\n")
            if user is None:
                user = User(email=email)
                db.add(user)
            else:
                db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
            user.password_hash = hash_password(password)
            user.role = "admin"
            user.active = True
            user.must_change_password = True
            user.totp_enabled = False
            user.totp_secret = None
            user.totp_counter = -1
            user.failed_logins = 0
            user.locked_until = None
            db.commit()
        except Exception:
            path.unlink(missing_ok=True)
            raise
        print("Administrator initialized; temporary password saved in the protected file")


if __name__ == "__main__":
    main()
