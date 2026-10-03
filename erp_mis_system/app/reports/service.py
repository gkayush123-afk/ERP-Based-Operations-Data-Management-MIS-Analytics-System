from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..models import AttendanceRecord, Department, Employee

STATUS_VALUES = ("present", "absent", "leave", "half_day")
STATUS_LABELS = {
    "present": "Present",
    "absent": "Absent",
    "leave": "Leave",
    "half_day": "Half-day",
}


def report_period(report_type, period_date=None, period_month=None, start_date=None, end_date=None):
    if report_type == "daily":
        if period_date is None:
            raise ValueError("Select a date for the daily report.")
        return period_date, period_date
    if report_type == "weekly":
        if period_date is None:
            raise ValueError("Select a date in the week to report.")
        start = period_date - timedelta(days=period_date.weekday())
        return start, start + timedelta(days=6)
    if report_type == "monthly":
        if period_month is None:
            raise ValueError("Select a month for the monthly report.")
        start = period_month.replace(day=1)
        return start, start.replace(day=monthrange(start.year, start.month)[1])
    if report_type == "department":
        if start_date is None or end_date is None:
            raise ValueError("Select both dates for the department summary.")
        if start_date > end_date:
            raise ValueError("Start date must not be after end date.")
        return start_date, end_date
    raise ValueError("Choose a valid report type.")


def _attendance_filter(start_date, end_date, department_id=None, status=None):
    conditions = [
        AttendanceRecord.attendance_date >= start_date,
        AttendanceRecord.attendance_date <= end_date,
        Employee.deleted_at.is_(None),
    ]
    if department_id is not None:
        conditions.append(Employee.department_id == department_id)
    if status in STATUS_VALUES:
        conditions.append(AttendanceRecord.status == status)
    return conditions


def _status_columns():
    return [
        func.coalesce(
            func.sum(case((AttendanceRecord.status == status, 1), else_=0)), 0
        ).label(status)
        for status in STATUS_VALUES
    ]


def build_report(session: Session, report_type, start_date, end_date, department_id=None, status=None):
    conditions = _attendance_filter(start_date, end_date, department_id, status)
    if report_type == "department":
        status_conditions = _attendance_filter(
            start_date, end_date, department_id, status
        )
        attendance_totals = (
            select(Department.id.label("department_id"), *_status_columns())
            .join(Employee, Employee.department_id == Department.id)
            .join(AttendanceRecord, AttendanceRecord.employee_id == Employee.id)
            .where(*status_conditions)
            .group_by(Department.id)
            .subquery()
        )
        present_totals = (
            select(
                Department.id.label("department_id"),
                func.count(AttendanceRecord.id).label("present"),
            )
            .join(Employee, Employee.department_id == Department.id)
            .join(AttendanceRecord, AttendanceRecord.employee_id == Employee.id)
            .where(
                *_attendance_filter(start_date, end_date, department_id),
                AttendanceRecord.status == "present",
            )
            .group_by(Department.id)
            .subquery()
        )
        active_employee_totals = (
            select(
                Employee.department_id.label("department_id"),
                func.count(Employee.id).label("active_employees"),
            )
            .where(
                Employee.deleted_at.is_(None),
                Employee.status == "active",
                Employee.joining_date <= end_date,
                *([Employee.department_id == department_id] if department_id is not None else []),
            )
            .group_by(Employee.department_id)
            .subquery()
        )
        query = (
            select(
                Department.name.label("department"),
                func.coalesce(attendance_totals.c.present, 0).label("present"),
                func.coalesce(attendance_totals.c.absent, 0).label("absent"),
                func.coalesce(attendance_totals.c.leave, 0).label("leave"),
                func.coalesce(attendance_totals.c.half_day, 0).label("half_day"),
                func.coalesce(present_totals.c.present, 0).label("present_for_percentage"),
                func.coalesce(active_employee_totals.c.active_employees, 0).label(
                    "active_employees"
                ),
            )
            .outerjoin(attendance_totals, attendance_totals.c.department_id == Department.id)
            .outerjoin(present_totals, present_totals.c.department_id == Department.id)
            .outerjoin(
                active_employee_totals,
                active_employee_totals.c.department_id == Department.id,
            )
            .where(Department.is_active.is_(True))
        )
        if department_id is not None:
            query = query.where(Department.id == department_id)
        raw_rows = session.execute(query.order_by(Department.name)).all()
        expected_days = (end_date - start_date).days + 1
        rows = []
        for row in raw_rows:
            expected = row.active_employees * expected_days
            rows.append(
                {
                    "department": row.department,
                    "present": int(row.present),
                    "absent": int(row.absent),
                    "leave": int(row.leave),
                    "half_day": int(row.half_day),
                    "active_employees": int(row.active_employees),
                    "attendance_percentage": round(row.present_for_percentage * 100 / expected, 2)
                    if expected
                    else 0.0,
                    "expected_employee_days": expected,
                }
            )
        return rows

    query = (
        select(
            AttendanceRecord.attendance_date.label("report_date"),
            Department.name.label("department"),
            *_status_columns(),
        )
        .join(Employee, Employee.id == AttendanceRecord.employee_id)
        .join(Department, Department.id == Employee.department_id)
        .where(*conditions)
        .group_by(AttendanceRecord.attendance_date, Department.name)
        .order_by(AttendanceRecord.attendance_date, Department.name)
    )
    return [
        {
            "date": row.report_date.isoformat(),
            "department": row.department,
            **{status_name: int(getattr(row, status_name)) for status_name in STATUS_VALUES},
        }
        for row in session.execute(query)
    ]


def build_dashboard_metrics(session: Session, department_id=None, today=None):
    today = today or date.today()
    employee_conditions = [
        Employee.deleted_at.is_(None),
        Employee.status == "active",
    ]
    attendance_conditions = [Employee.deleted_at.is_(None)]
    if department_id is not None:
        employee_conditions.append(Employee.department_id == department_id)
        attendance_conditions.append(Employee.department_id == department_id)

    total_employees = session.scalar(
        select(func.count(Employee.id)).where(*employee_conditions)
    ) or 0
    today_attendance = session.execute(
        select(AttendanceRecord.status, func.count(AttendanceRecord.id))
        .join(Employee, Employee.id == AttendanceRecord.employee_id)
        .where(
            *attendance_conditions,
            AttendanceRecord.attendance_date == today,
        )
        .group_by(AttendanceRecord.status)
    ).all()
    today_counts = {status: 0 for status in STATUS_VALUES}
    for status, count in today_attendance:
        if status in today_counts:
            today_counts[status] = int(count)

    pending_query = (
        select(func.count(AttendanceRecord.id))
        .join(Employee, Employee.id == AttendanceRecord.employee_id)
        .where(
            *attendance_conditions,
            AttendanceRecord.verification_status == "pending",
        )
    )
    pending_verifications = int(session.scalar(pending_query) or 0)

    department_employee_counts = {
        name: int(count)
        for name, count in session.execute(
            select(Department.name, func.count(Employee.id))
            .join(Employee, Employee.department_id == Department.id)
            .where(
                Employee.deleted_at.is_(None),
                Employee.status == "active",
                Department.is_active.is_(True),
                *([Employee.department_id == department_id] if department_id is not None else []),
            )
            .group_by(Department.name)
        )
    }
    department_performance = []
    total_active_department_employees = sum(department_employee_counts.values())
    for name, count in sorted(department_employee_counts.items()):
        department_performance.append(
            {
                "name": name,
                "employees": count,
                "percent": round((count / total_active_department_employees) * 100, 1)
                if total_active_department_employees
                else 0,
            }
        )

    monthly_sales = total_employees * 950 + sum(today_counts.values()) * 220
    purchase_amount = total_employees * 640 + pending_verifications * 180
    completed_tasks = today_counts.get("present", 0) + today_counts.get("leave", 0)

    department_chart_query = (
        select(Department.name, AttendanceRecord.status, func.count(AttendanceRecord.id))
        .join(Employee, Employee.department_id == Department.id)
        .join(AttendanceRecord, AttendanceRecord.employee_id == Employee.id)
        .where(
            Employee.deleted_at.is_(None),
            AttendanceRecord.attendance_date == today,
            Department.is_active.is_(True),
            *([Employee.department_id == department_id] if department_id is not None else []),
        )
        .group_by(Department.name, AttendanceRecord.status)
        .order_by(Department.name, AttendanceRecord.status)
    )
    department_names = []
    department_series = {status: [] for status in STATUS_VALUES}
    departments = {}
    for name, status, count in session.execute(department_chart_query):
        if name not in departments:
            departments[name] = {key: 0 for key in STATUS_VALUES}
        if status in departments[name]:
            departments[name][status] = int(count)
    department_names = list(departments)
    for name in department_names:
        for status in STATUS_VALUES:
            department_series[status].append(departments[name][status])

    month_start = today.replace(day=1)
    first_month = (month_start.replace(day=1) - timedelta(days=1)).replace(day=1)
    for _ in range(10):
        first_month = (first_month - timedelta(days=1)).replace(day=1)
    trend_start = first_month
    year = func.extract("year", AttendanceRecord.attendance_date)
    month = func.extract("month", AttendanceRecord.attendance_date)
    monthly_query = (
        select(year.label("year"), month.label("month"), AttendanceRecord.status, func.count(AttendanceRecord.id))
        .join(Employee, Employee.id == AttendanceRecord.employee_id)
        .where(
            *attendance_conditions,
            AttendanceRecord.attendance_date >= trend_start,
            AttendanceRecord.attendance_date <= today,
        )
        .group_by(year, month, AttendanceRecord.status)
    )
    monthly_counts = {}
    for report_year, report_month, status, count in session.execute(monthly_query):
        key = (int(report_year), int(report_month))
        monthly_counts.setdefault(key, {key: 0 for key in STATUS_VALUES})
        if status in monthly_counts[key]:
            monthly_counts[key][status] = int(count)

    trend_labels = []
    trend_series = {status: [] for status in STATUS_VALUES}
    cursor = trend_start
    for _ in range(12):
        key = (cursor.year, cursor.month)
        trend_labels.append(cursor.strftime("%b %Y"))
        for status in STATUS_VALUES:
            trend_series[status].append(monthly_counts.get(key, {}).get(status, 0))
        cursor = (cursor.replace(day=monthrange(cursor.year, cursor.month)[1]) + timedelta(days=1))

    return {
        "total_employees": int(total_employees),
        "today_attendance": today_counts,
        "today_attendance_total": sum(today_counts.values()),
        "pending_verifications": pending_verifications,
        "completed_tasks": int(completed_tasks),
        "monthly_sales": int(monthly_sales),
        "purchase_amount": int(purchase_amount),
        "department_performance": department_performance,
        "department_labels": department_names,
        "department_series": department_series,
        "trend_labels": trend_labels,
        "trend_series": trend_series,
    }
