"""Backend admin commands.

    python -m backend.manage create-user admin
    python -m backend.manage list-users
"""
import getpass
import sqlite3
import sys

from backend.app import create_user, db


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "create-user":
        password = getpass.getpass(f"Password for {sys.argv[2]}: ")
        if len(password) < 6:
            sys.exit("Use at least 6 characters.")
        if getpass.getpass("Repeat password: ") != password:
            sys.exit("Passwords don't match.")
        try:
            create_user(sys.argv[2], password)
        except sqlite3.IntegrityError:
            sys.exit(f"User {sys.argv[2]} already exists.")
        print(f"User {sys.argv[2]} created.")
    elif len(sys.argv) == 2 and sys.argv[1] == "list-users":
        for u in db.all("SELECT username, created FROM users"):
            print(f"{u['username']}  (created {u['created']})")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
