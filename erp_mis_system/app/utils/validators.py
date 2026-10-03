from collections import defaultdict
from datetime import date, time
from typing import Any


EMPLOYEE_REQUIRED_FIELDS = (
    "employee_code",
    "full_name",
    "email",
    "designation",
    "department_id",
    "joining_date",
    "status",
)
ATTENDANCE_REQUIRED_FIELDS = (
    "employee_id",
    "attendance_date",
    "status",
    "verification_status",
    "created_by",
)


def missing_required_fields(values: Any, fields: tuple[str, ...]) -> list[str]:
    """Return field names missing or blank on a mapping or model-like object."""
    missing = []
    for field in fields:
        value = values.get(field) if isinstance(values, dict) else getattr(values, field, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return missing


def duplicate_groups(records: list[Any], field: str) -> list[list[Any]]:
    """Group records whose selected text value repeats, case-insensitively."""
    groups: dict[str, list[Any]] = defaultdict(list)
    for record in records:
        value = record.get(field) if isinstance(record, dict) else getattr(record, field, None)
        normalized = str(value or "").strip().casefold()
        if normalized:
            groups[normalized].append(record)
    return [group for group in groups.values() if len(group) > 1]


def duplicate_attendance_groups(records: list[Any]) -> list[list[Any]]:
    """Group attendance rows sharing the employee and attendance date."""
    groups: dict[tuple[Any, Any], list[Any]] = defaultdict(list)
    for record in records:
        if isinstance(record, dict):
            employee_id = record.get("employee_id")
            attendance_date = record.get("attendance_date")
        else:
            employee_id = getattr(record, "employee_id", None)
            attendance_date = getattr(record, "attendance_date", None)
        if employee_id is not None and attendance_date is not None:
            groups[(employee_id, attendance_date)].append(record)
    return [group for group in groups.values() if len(group) > 1]


def invalid_time_range(in_time: time | None, out_time: time | None) -> bool:
    return in_time is not None and out_time is not None and out_time < in_time


def is_future_date(value: date | None, today: date | None = None) -> bool:
    return value is not None and value > (today or date.today())
