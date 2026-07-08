"""Create or reset an admin user. Usage:
  python create_admin.py you@company.com your-password
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from auth import hash_password
from db import SessionLocal, User, init_db


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python create_admin.py <email> <password>")
        sys.exit(1)
    email = sys.argv[1].strip().lower()
    password = sys.argv[2]
    if len(password) < 6:
        print("Password must be at least 6 characters.")
        sys.exit(1)

    init_db()
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email))
        if user:
            user.password_hash = hash_password(password)
            user.role = "admin"
            user.is_active = True
            print(f"Updated existing user {email} to admin.")
        else:
            db.add(
                User(
                    email=email,
                    name=email.split("@")[0],
                    password_hash=hash_password(password),
                    role="admin",
                    credits_balance=0,
                    is_active=True,
                )
            )
            print(f"Created admin user {email}.")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
