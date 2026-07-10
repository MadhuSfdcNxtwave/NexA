"""Reset a user's password: python reset_user_password.py email newpassword"""
from __future__ import annotations

import sys

from sqlalchemy import select

from auth import hash_password
from db import SessionLocal, User


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python reset_user_password.py <email> <new-password>")
        print("Existing users:")
        db = SessionLocal()
        for u in db.scalars(select(User).order_by(User.id)).all():
            print(f"  {u.email}  (active={u.is_active}, role={u.role})")
        db.close()
        sys.exit(1)

    email = sys.argv[1].strip().lower()
    password = sys.argv[2]
    if len(password) < 6:
        print("Password must be at least 6 characters")
        sys.exit(1)

    db = SessionLocal()
    user = db.scalar(select(User).where(User.email == email))
    if not user:
        print(f"No user with email: {email}")
        sys.exit(1)
    user.password_hash = hash_password(password)
    user.is_active = True
    db.commit()
    print(f"OK — password reset for {user.email}. They can log in now.")
    db.close()


if __name__ == "__main__":
    main()
