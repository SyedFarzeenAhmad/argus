"""Management commands.

uv run python -m argus_api.cli create-user eng.rao --role engineer
uv run python -m argus_api.cli init-db        # create tables without Alembic (SQLite demo)
"""

from __future__ import annotations

import argparse
import getpass
import os

from argus_api.core.auth import ROLES, hash_password
from argus_api.db import session as db_session
from argus_api.db.models import AppUser, Base


def main() -> None:
    ap = argparse.ArgumentParser(description="ARGUS management")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("create-user")
    u.add_argument("username")
    u.add_argument("--role", choices=ROLES, required=True)
    u.add_argument("--password", help="default: $ARGUS_NEW_PASSWORD or prompt")
    sub.add_parser("init-db")
    args = ap.parse_args()

    if args.cmd == "init-db":
        Base.metadata.create_all(db_session.get_engine())
        print("tables created")
        return

    password = args.password or os.environ.get("ARGUS_NEW_PASSWORD") or getpass.getpass()
    if len(password) < 8:
        raise SystemExit("password must be at least 8 characters")
    with db_session.session_scope() as s:
        user = s.get(AppUser, args.username)
        if user is None:
            s.add(
                AppUser(
                    username=args.username, password_hash=hash_password(password), role=args.role
                )
            )
        else:
            user.password_hash, user.role, user.active = hash_password(password), args.role, True
    print(f"{args.username} ({args.role}) ready")


if __name__ == "__main__":
    main()
