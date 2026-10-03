from datetime import date

from sqlalchemy import func, select

from app.extensions import db
from app.models import AttendanceRecord, Employee
from seed_demo import seed_demo_data


def test_demo_seed_creates_five_departments_fifty_employees_and_weekday_attendance(app):
    with app.app_context():
        first_result = seed_demo_data(db.session, today=date(2026, 10, 3))
        assert first_result == {
            "departments": 5,
            "employees": 50,
            "attendance_month": "2026-09",
            "workdays": 22,
            "new_attendance_records": 1100,
        }
        employees = db.session.scalars(
            select(Employee).where(Employee.employee_code.like("DEMO-%"))
        ).all()
        assert len(employees) == 50
        attendance_count = db.session.scalar(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.employee_id.in_([employee.id for employee in employees]),
                AttendanceRecord.attendance_date >= date(2026, 9, 1),
                AttendanceRecord.attendance_date <= date(2026, 9, 30),
            )
        )
        assert attendance_count == 1100
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
        assert status_counts == {
            "present": 991,
            "absent": 39,
            "leave": 35,
            "half_day": 35,
        }

        second_result = seed_demo_data(db.session, today=date(2026, 10, 3))
        assert second_result["new_attendance_records"] == 0
        assert db.session.scalar(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.employee_id.in_([employee.id for employee in employees]),
                AttendanceRecord.attendance_date >= date(2026, 9, 1),
                AttendanceRecord.attendance_date <= date(2026, 9, 30),
            )
        ) == 1100
