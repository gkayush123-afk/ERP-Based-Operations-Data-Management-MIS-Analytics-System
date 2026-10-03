import os
import re
import sys

from dotenv import load_dotenv
from sqlalchemy import or_
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import Department, User


def strong_enough(password):
    groups = (
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"\d", password)),
        bool(re.search(r"[^A-Za-z0-9]", password)),
    )
    return len(password) >= 12 and sum(groups) >= 3


def main():
    load_dotenv()
    username = os.getenv("ADMIN_USERNAME", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "")
    email = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()
    full_name = os.getenv("ADMIN_FULL_NAME", "System Administrator").strip()

    if not username or not password:
        sys.exit("Set ADMIN_USERNAME and ADMIN_PASSWORD in .env before running seed.py.")
    if not strong_enough(password):
        sys.exit(
            "ADMIN_PASSWORD must be at least 12 characters and use at least three "
            "character groups (lowercase, uppercase, number, symbol)."
        )

    app = create_app()
    with app.app_context():
        db.create_all()
        department = db.session.scalar(
            db.select(Department).where(Department.name == "General")
        )
        if department is None:
            department = Department(name="General", is_active=True)
            db.session.add(department)
            db.session.flush()

        admin = db.session.scalar(
            db.select(User).where(or_(User.username == username, User.email == email))
        )
        if admin is not None:
            if admin.username != username or admin.email != email or admin.role != "admin":
                sys.exit("ADMIN_USERNAME or ADMIN_EMAIL conflicts with an existing account.")
            print(f"Active admin account already exists; left unchanged: {username}")
            return

        admin = User(
            full_name=full_name,
            username=username,
            email=email,
            department_id=department.id,
            role="admin",
            status="active",
            password_hash=generate_password_hash(password),
        )
        db.session.add(admin)
        db.session.commit()
        print(f"Created active admin account: {username}")


if __name__ == "__main__":
    main()
