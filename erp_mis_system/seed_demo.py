import os
import re
from calendar import monthrange
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, insert, or_, select

from app import create_app
from app.extensions import db
from app.models import AttendanceRecord, Department, Employee, User

DEMO_DEPARTMENT = "Operations"
DEMO_EMPLOYEE_COUNT = 5
FIRST_NAMES = ("Aarav", "Aditi", "Ananya", "Arjun", "Dev")
LAST_NAMES = ("Sharma", "Patel", "Rao", "Mehta", "Nair")
DEMO_ROLES = {
    "manager": ("manager", "demo_manager", "demo-manager@example.com", "Demo Manager"),
    "staff": ("data_entry", "demo_staff", "demo-staff@example.com", "Demo Staff"),
}


def demo_credentials_from_environment():
    credentials = {}
    for role, role_defaults in DEMO_ROLES.items():
        prefix = f"DEMO_{role.upper()}"
        password = os.getenv(f"{prefix}_PASSWORD", "")
        if not password:
            continue
        credentials[role] = {
            "username": os.getenv(f"{prefix}_USERNAME", role_defaults[1]).strip(),
            "email": os.getenv(f"{prefix}_EMAIL", role_defaults[2]).strip().lower(),
            "full_name": os.getenv(f"{prefix}_FULL_NAME", role_defaults[3]).strip(),
            "password": password,
        }
    return credentials


def _password_is_strong_enough(password):
    groups = (
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"\d", password)),
        bool(re.search(r"[^A-Za-z0-9]", password)),
    )
    return len(password) >= 12 and sum(groups) >= 3


def seed_demo_accounts(session, department, credentials):
    accounts = {}
    for role, details in credentials.items():
        if role not in DEMO_ROLES:
            raise ValueError(f"Unsupported demo account role: {role}")
        username = (details.get("username") or "").strip()
        email = (details.get("email") or "").strip().lower()
        full_name = (details.get("full_name") or "").strip()
        password = details.get("password") or ""
        if not all((username, email, full_name, password)):
            raise ValueError(f"Complete username, email, full name, and password for demo {role}.")
        if not _password_is_strong_enough(password):
            raise ValueError(
                f"DEMO_{role.upper()}_PASSWORD must be at least 12 characters and use "
                "at least three character groups."
            )

        database_role = DEMO_ROLES[role][0]
        user = session.scalar(
            select(User).where(
                or_(
                    func.lower(User.username) == username.lower(),
                    func.lower(User.email) == email,
                )
            )
        )
        if user is not None and (
            user.username.lower() != username.lower()
            or user.email.lower() != email
            or user.role != database_role
        ):
            raise ValueError(
                f"Configured demo {role} username or email conflicts with another account."
            )
        if user is None:
            user = User(username=username, email=email, role=database_role)
            session.add(user)

        user.username = username
        user.email = email
        user.full_name = full_name
        user.department_id = department.id
        user.role = database_role
        user.status = "active"
        user.must_change_password = False
        user.set_password(password)
        session.flush()
        accounts[role] = user
    return accounts


def seed_demo_data(session, today=None, demo_credentials=None):
    today = today or date.today()
    previous_month_end = today.replace(day=1) - timedelta(days=1)
    month_start = previous_month_end.replace(day=1)

    department = session.scalar(
        select(Department).where(Department.name == DEMO_DEPARTMENT)
    )
    if department is None:
        department = Department(name=DEMO_DEPARTMENT, is_active=True)
        session.add(department)
        session.flush()
    department.is_active = True

    demo_accounts = seed_demo_accounts(session, department, demo_credentials or {})
    admin_id = session.scalar(
        select(User.id)
        .where(User.role == "admin", User.status == "active")
        .order_by(User.id)
        .limit(1)
    )
    manager_id = demo_accounts.get("manager").id if demo_accounts.get("manager") else admin_id
    staff_id = demo_accounts.get("staff").id if demo_accounts.get("staff") else admin_id

    employees = []
    for employee_number in range(1, DEMO_EMPLOYEE_COUNT + 1):
        code = f"DEMO-OPS-{employee_number:03d}"
        employee = session.scalar(
            select(Employee).where(Employee.employee_code == code)
        )
        if employee is None:
            employee = Employee(
                employee_code=code,
                full_name=f"{FIRST_NAMES[employee_number - 1]} {LAST_NAMES[employee_number - 1]}",
                email=f"{code.lower()}@example.com",
                phone=f"+91-90000-000{employee_number}",
                designation=("Analyst", "Coordinator", "Associate", "Executive", "Specialist")[
                    employee_number - 1
                ],
                department_id=department.id,
                joining_date=date(2020 + employee_number, 1, 1),
                status="active",
                created_by=admin_id,
            )
            session.add(employee)
            session.flush()
        employees.append(employee)

    existing_keys = set(
        session.execute(
            select(AttendanceRecord.employee_id, AttendanceRecord.attendance_date).where(
                AttendanceRecord.employee_id.in_([employee.id for employee in employees]),
                AttendanceRecord.attendance_date >= month_start,
                AttendanceRecord.attendance_date <= previous_month_end,
            )
        ).all()
    )

    attendance_rows = []
    workday_number = 0
    for day_number in range(1, monthrange(month_start.year, month_start.month)[1] + 1):
        attendance_date = month_start.replace(day=day_number)
        if attendance_date.weekday() >= 5:
            continue
        for employee_number, employee in enumerate(employees, start=1):
            if (employee.id, attendance_date) in existing_keys:
                continue
            selector = (employee_number * 7 + attendance_date.day) % 17
            status = (
                "absent"
                if selector == 0
                else "leave"
                if selector == 1
                else "half_day"
                if selector == 2
                else "present"
            )
            in_time = time(9, (employee_number * 3 + attendance_date.day) % 31)
            out_time = time(13, 0) if status == "half_day" else time(17, 15)
            if status == "absent":
                in_time = None
                out_time = None
            attendance_rows.append(
                {
                    "employee_id": employee.id,
                    "attendance_date": attendance_date,
                    "status": status,
                    "in_time": in_time,
                    "out_time": out_time,
                    "remarks": "Seeded demonstration record",
                    "verification_status": "verified",
                    "verified_by": manager_id,
                    "verified_at": datetime.combine(attendance_date, time(18, 0)),
                    "created_by": staff_id,
                    "created_at": datetime.combine(attendance_date, time(8, 45)),
                    "updated_at": datetime.combine(attendance_date, time(18, 0)),
                }
            )
        workday_number += 1

    if attendance_rows:
        session.execute(insert(AttendanceRecord), attendance_rows)

    pending_date = today
    while pending_date.weekday() >= 5:
        pending_date -= timedelta(days=1)
    pending_employee = employees[0]
    pending_exists = session.scalar(
        select(AttendanceRecord.id).where(
            AttendanceRecord.employee_id == pending_employee.id,
            AttendanceRecord.attendance_date == pending_date,
        )
    )
    new_pending_records = 0
    if pending_exists is None:
        session.add(
            AttendanceRecord(
                employee_id=pending_employee.id,
                attendance_date=pending_date,
                status="present",
                in_time=time(9, 0),
                remarks="Demo record awaiting manager review",
                verification_status="pending",
                created_by=staff_id,
                created_at=datetime.combine(pending_date, time(9, 5)),
                updated_at=datetime.combine(pending_date, time(9, 5)),
            )
        )
        new_pending_records = 1

    session.commit()
    return {
        "departments": 1,
        "employees": len(employees),
        "attendance_month": month_start.strftime("%Y-%m"),
        "workdays": workday_number,
        "new_attendance_records": len(attendance_rows),
        "new_pending_records": new_pending_records,
        "demo_accounts": len(demo_accounts),
    }


def main():
    app = create_app()
    with app.app_context():
        results = seed_demo_data(
            db.session,
            demo_credentials=demo_credentials_from_environment(),
        )
    print(
        "Demo data ready: "
        f"{results['departments']} department, {results['employees']} employees, "
        f"{results['new_attendance_records']} new verified attendance records for "
        f"{results['attendance_month']}, {results['new_pending_records']} new pending review record(s), "
        f"{results['demo_accounts']} demo role account(s) configured. Existing records are preserved."
    )
    if results["demo_accounts"] < 2:
        print(
            "To create both demo logins, set DEMO_MANAGER_PASSWORD and DEMO_STAFF_PASSWORD "
            "in the environment, then run seed_demo.py again."
        )


if __name__ == "__main__":
    main()
