from datetime import date

from sqlalchemy import func, select

from app.extensions import db
from app.models import AttendanceRecord, Employee, User
from seed_demo import seed_demo_data


def test_demo_seed_creates_a_small_department_dataset_and_pending_review(app):
    with app.app_context():
        first_result = seed_demo_data(db.session, today=date(2026, 10, 3))
        assert first_result == {
            "departments": 1,
            "employees": 5,
            "attendance_month": "2026-09",
            "workdays": 22,
            "new_attendance_records": 110,
            "new_pending_records": 1,
            "demo_accounts": 0,
        }
        employees = db.session.scalars(
            select(Employee).where(Employee.employee_code.like("DEMO-%"))
        ).all()
        assert len(employees) == 5
        attendance_count = db.session.scalar(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.employee_id.in_([employee.id for employee in employees]),
                AttendanceRecord.attendance_date >= date(2026, 9, 1),
                AttendanceRecord.attendance_date <= date(2026, 9, 30),
            )
        )
        assert attendance_count == 110
        status_counts = dict(
            db.session.execute(
                select(AttendanceRecord.status, func.count(AttendanceRecord.id))
                .where(
                    AttendanceRecord.employee_id.in_([employee.id for employee in employees]),
                    AttendanceRecord.attendance_date >= date(2026, 9, 1),
                    AttendanceRecord.attendance_date <= date(2026, 9, 30),
                )
                .group_by(AttendanceRecord.status)
            ).all()
        )
        assert sum(status_counts.values()) == 110
        pending = db.session.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.employee_id.in_([employee.id for employee in employees]),
                AttendanceRecord.verification_status == "pending",
            )
        )
        assert pending is not None
        assert pending.attendance_date == date(2026, 10, 2)

        second_result = seed_demo_data(db.session, today=date(2026, 10, 3))
        assert second_result["new_attendance_records"] == 0
        assert second_result["new_pending_records"] == 0
        assert db.session.scalar(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.employee_id.in_([employee.id for employee in employees]),
                AttendanceRecord.attendance_date >= date(2026, 9, 1),
                AttendanceRecord.attendance_date <= date(2026, 9, 30),
            )
        ) == 110


def test_demo_seed_creates_active_manager_and_staff_accounts(app):
    credentials = {
        "manager": {
            "username": "demo_manager",
            "email": "demo-manager@example.com",
            "full_name": "Demo Manager",
            "password": "Demo-Manager-2026!",
        },
        "staff": {
            "username": "demo_staff",
            "email": "demo-staff@example.com",
            "full_name": "Demo Staff",
            "password": "Demo-Staff-2026!",
        },
    }
    with app.app_context():
        result = seed_demo_data(
            db.session,
            today=date(2026, 10, 3),
            demo_credentials=credentials,
        )
        assert result["demo_accounts"] == 2
        for username, role in (("demo_manager", "manager"), ("demo_staff", "data_entry")):
            user = db.session.scalar(select(User).where(User.username == username))
            assert user is not None
            assert user.role == role
            assert user.status == "active"
            assert user.department.name == "Operations"
            assert not user.must_change_password
            credential_role = "staff" if role == "data_entry" else role
            assert user.check_password(credentials[credential_role]["password"])

        pending = db.session.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.verification_status == "pending"
            )
        )
        assert pending.created_by == db.session.scalar(
            select(User.id).where(User.username == "demo_staff")
        )
