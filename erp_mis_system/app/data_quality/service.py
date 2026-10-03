from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import AttendanceRecord, Department, Employee
from ..utils.validators import (
    ATTENDANCE_REQUIRED_FIELDS,
    EMPLOYEE_REQUIRED_FIELDS,
    duplicate_attendance_groups,
    duplicate_groups,
    invalid_time_range,
    is_future_date,
    missing_required_fields,
)


def _value(record: Any, field: str) -> Any:
    return record.get(field) if isinstance(record, dict) else getattr(record, field, None)


def _issue(code, message, entity, entity_id, fix_url, extra=None):
    return {
        "code": code,
        "message": message,
        "entity": entity,
        "entity_id": entity_id,
        "fix_url": fix_url,
        "extra": extra or {},
    }


def build_data_quality_report(
    session: Session,
    start_date: date,
    end_date: date,
    department_id: int | None = None,
    today: date | None = None,
) -> dict[str, list[dict]]:
    """Collect data-quality issues for a date range and optional department."""
    today = today or date.today()
    employee_query = session.query(Employee).filter(Employee.deleted_at.is_(None))
    if department_id is not None:
        employee_query = employee_query.filter(Employee.department_id == department_id)
    employees = employee_query.order_by(Employee.id).all()

    employee_ids = [employee.id for employee in employees]
    attendance_query = session.query(AttendanceRecord)
    if employee_ids:
        attendance_query = attendance_query.filter(AttendanceRecord.employee_id.in_(employee_ids))
    else:
        attendance_query = attendance_query.filter(False)
    attendance = attendance_query.order_by(AttendanceRecord.id).all()

    issues: dict[str, list[dict]] = defaultdict(list)

    for employee in employees:
        missing = missing_required_fields(employee, EMPLOYEE_REQUIRED_FIELDS)
        for field in missing:
            issues["missing"].append(
                _issue(
                    "employee_missing_field",
                    f"Employee is missing mandatory field: {field.replace('_', ' ')}.",
                    "employee",
                    employee.id,
                    f"/employees/{employee.id}/edit",
                    {"field": field, "employee_code": employee.employee_code or f"ID {employee.id}"},
                )
            )
        if is_future_date(employee.joining_date, today):
            issues["invalid"].append(
                _issue(
                    "employee_future_joining_date",
                    "Employee joining date is in the future.",
                    "employee",
                    employee.id,
                    f"/employees/{employee.id}/edit",
                    {"employee_code": employee.employee_code or f"ID {employee.id}"},
                )
            )

    for field, label in (("employee_code", "employee code"), ("email", "email")):
        for group in duplicate_groups(employees, field):
            display_value = _value(group[0], field)
            for employee in group:
                issues["duplicates"].append(
                    _issue(
                        f"duplicate_employee_{field}",
                        f"Duplicate employee {label}: {display_value}.",
                        "employee",
                        employee.id,
                        f"/employees/{employee.id}/edit",
                        {"value": display_value, "employee_code": employee.employee_code},
                    )
                )

    for record in attendance:
        employee = record.employee
        missing = missing_required_fields(record, ATTENDANCE_REQUIRED_FIELDS)
        for field in missing:
            issues["missing"].append(
                _issue(
                    "attendance_missing_field",
                    f"Attendance record is missing mandatory field: {field.replace('_', ' ')}.",
                    "attendance",
                    record.id,
                    f"/attendance/{record.id}/edit",
                    {
                        "employee_code": employee.employee_code if employee else "Unknown",
                        "attendance_date": record.attendance_date.isoformat() if record.attendance_date else "",
                        "field": field,
                    },
                )
            )
        if invalid_time_range(record.in_time, record.out_time):
            issues["invalid"].append(
                _issue(
                    "attendance_invalid_time_range",
                    "Attendance out time is earlier than in time.",
                    "attendance",
                    record.id,
                    f"/attendance/{record.id}/edit",
                    {
                        "employee_code": employee.employee_code if employee else "Unknown",
                        "attendance_date": record.attendance_date.isoformat(),
                    },
                )
            )
        if is_future_date(record.attendance_date, today):
            issues["invalid"].append(
                _issue(
                    "attendance_future_date",
                    "Attendance date is in the future.",
                    "attendance",
                    record.id,
                    f"/attendance/{record.id}/edit",
                    {
                        "employee_code": employee.employee_code if employee else "Unknown",
                        "attendance_date": record.attendance_date.isoformat(),
                    },
                )
            )

    for group in duplicate_attendance_groups(attendance):
        record = group[0]
        employee = record.employee
        for duplicate in group:
            issues["duplicates"].append(
                _issue(
                    "duplicate_attendance_employee_date",
                    "More than one attendance record exists for this employee and date.",
                    "attendance",
                    duplicate.id,
                    f"/attendance/{duplicate.id}/edit",
                    {
                        "employee_code": employee.employee_code if employee else "Unknown",
                        "attendance_date": record.attendance_date.isoformat(),
                    },
                )
            )

    scoped_employee_ids = [employee.id for employee in employees if employee.status == "active"]
    present_employee_ids = set()
    if scoped_employee_ids:
        present_employee_ids = set(
            session.query(AttendanceRecord.employee_id)
            .filter(
                AttendanceRecord.employee_id.in_(scoped_employee_ids),
                AttendanceRecord.attendance_date >= start_date,
                AttendanceRecord.attendance_date <= end_date,
            )
            .distinct()
            .all()
        )
        present_employee_ids = {item[0] for item in present_employee_ids}

    for employee in employees:
        if employee.status != "active" or employee.id in present_employee_ids:
            continue
        issues["missing"].append(
            _issue(
                "employee_missing_attendance",
                f"No attendance entry exists between {start_date.isoformat()} and {end_date.isoformat()}.",
                "employee",
                employee.id,
                f"/attendance/create?employee_id={employee.id}&attendance_date={start_date.isoformat()}",
                {
                    "employee_code": employee.employee_code or f"ID {employee.id}",
                    "date_range": f"{start_date.isoformat()} to {end_date.isoformat()}",
                },
            )
        )

    return {category: list(category_issues) for category, category_issues in issues.items()}


def summarize_data_quality(issues: dict[str, list[dict]]) -> dict[str, int]:
    counts = {
        category: len(issues.get(category, []))
        for category in ("missing", "duplicates", "invalid")
    }
    counts["total"] = sum(counts.values())
    return counts
