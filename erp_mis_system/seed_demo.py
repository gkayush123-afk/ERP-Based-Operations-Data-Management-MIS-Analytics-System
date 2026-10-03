from calendar import monthrange
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import AttendanceRecord, Department, Employee, User

DEMO_DEPARTMENTS = (
    "General",
    "Finance",
    "Human Resources",
    "Operations",
    "Sales",
)
FIRST_NAMES = (
    "Aarav", "Aditi", "Ananya", "Arjun", "Dev", "Diya", "Ishaan", "Kabir", "Meera", "Neha"
)
LAST_NAMES = (
    "Sharma", "Patel", "Rao", "Mehta", "Nair", "Kapoor", "Iyer", "Singh", "Das", "Khan"
)


def seed_demo_data(session, today=None):
    today = today or date.today()
    previous_month_end = today.replace(day=1) - timedelta(days=1)
    month_start = previous_month_end.replace(day=1)
    departments = {}
    for name in DEMO_DEPARTMENTS:
        department = session.scalar(select(Department).where(Department.name == name))
        if department is None:
            department = Department(name=name, is_active=True)
            session.add(department)
            session.flush()
        departments[name] = department

    admin_id = session.scalar(
        select(User.id)
        .where(User.role == "admin", User.status == "active")
        .order_by(User.id)
        .limit(1)
    )
    employees = []
    for department_index, department_name in enumerate(DEMO_DEPARTMENTS):
        prefix = "".join(part[0] for part in department_name.split()).upper()
        for employee_number in range(1, 11):
            code = f"DEMO-{prefix}-{employee_number:03d}"
            employee = session.scalar(
                select(Employee).where(Employee.employee_code == code)
            )
            if employee is None:
                employee = Employee(
                    employee_code=code,
                    full_name=(
                        f"{FIRST_NAMES[(employee_number - 1 + department_index) % 10]} "
                        f"{LAST_NAMES[(employee_number - 1 + department_index * 2) % 10]}"
                    ),
                    email=f"{code.lower()}@example.com",
                    phone=f"+91-90000-{department_index:02d}{employee_number:03d}",
                    designation=("Analyst", "Coordinator", "Associate", "Executive", "Specialist")[
                        (employee_number - 1) % 5
                    ],
                    department_id=departments[department_name].id,
                    joining_date=date(2020 + employee_number % 5, 1, 1),
                    status="active",
                    created_by=admin_id,
                )
                session.add(employee)
                session.flush()
            employees.append(employee)

    demo_employee_ids = [employee.id for employee in employees]
    existing_keys = set()
    if demo_employee_ids:
        existing_keys = set(
            session.execute(
                select(AttendanceRecord.employee_id, AttendanceRecord.attendance_date).where(
                    AttendanceRecord.employee_id.in_(demo_employee_ids),
                    AttendanceRecord.attendance_date >= month_start,
                    AttendanceRecord.attendance_date <= previous_month_end,
                )
            ).all()
        )

    attendance_rows = []
    new_attendance_records = 0
    workday_number = 0
    for day_number in range(1, monthrange(month_start.year, month_start.month)[1] + 1):
        attendance_date = month_start.replace(day=day_number)
        if attendance_date.weekday() >= 5:
            continue
        for employee_number, employee in enumerate(employees, start=1):
            key = (employee.id, attendance_date)
            if key in existing_keys:
                continue
            selector = (employee_number * 7 + attendance_date.day) % 29
            status = (
                "absent"
                if selector == 0
                else "leave"
                if selector == 1
                else "half_day"
                if selector == 2
                else "present"
            )
            in_time = None
            out_time = None
            if status in {"present", "half_day"}:
                minutes_late = (employee_number * 3 + attendance_date.day) % 31
                in_time = time(9, minutes_late)
                out_time = (
                    time(13, 0)
                    if status == "half_day"
                    else time(17, (employee_number + attendance_date.day) % 30)
                )
            attendance_rows.append(
                {
                    "employee_id": employee.id,
                    "attendance_date": attendance_date,
                    "status": status,
                    "in_time": in_time,
                    "out_time": out_time,
                    "remarks": "Seeded demonstration record",
                    "verification_status": "verified",
                    "verified_by": admin_id,
                    "verified_at": datetime.combine(attendance_date, time(18, 0)),
                    "created_by": admin_id,
                    "created_at": datetime.combine(attendance_date, time(8, 45)),
                    "updated_at": datetime.combine(attendance_date, time(18, 0)),
                }
            )
        workday_number += 1
        if len(attendance_rows) >= 250:
            new_attendance_records += len(attendance_rows)
            session.execute(db.insert(AttendanceRecord), attendance_rows)
            attendance_rows.clear()
    if attendance_rows:
        new_attendance_records += len(attendance_rows)
        session.execute(db.insert(AttendanceRecord), attendance_rows)
    session.commit()
    return {
        "departments": len(DEMO_DEPARTMENTS),
        "employees": len(employees),
        "attendance_month": month_start.strftime("%Y-%m"),
        "workdays": workday_number,
        "new_attendance_records": new_attendance_records,
    }


def main():
    app = create_app()
    with app.app_context():
        results = seed_demo_data(db.session)
    print(
        "Demo data ready: "
        f"{results['departments']} departments, {results['employees']} employees, "
        f"{results['new_attendance_records']} new attendance records for "
        f"{results['attendance_month']}; existing attendance is preserved."
    )


if __name__ == "__main__":
    main()
