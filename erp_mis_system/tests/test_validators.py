from datetime import date, time, timedelta

from app.utils.validators import (
    duplicate_attendance_groups,
    duplicate_groups,
    invalid_time_range,
    is_future_date,
    missing_required_fields,
)


def test_missing_required_fields_handles_mapping_and_model_like_objects():
    assert missing_required_fields(
        {"employee_code": " ", "email": None, "status": "active"},
        ("employee_code", "email", "status"),
    ) == ["employee_code", "email"]


def test_duplicate_groups_are_case_and_whitespace_insensitive():
    records = [
        {"employee_code": " E001 "},
        {"employee_code": "e001"},
        {"employee_code": "E002"},
        {"employee_code": ""},
    ]
    groups = duplicate_groups(records, "employee_code")
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_duplicate_attendance_groups_use_employee_and_date():
    day = date(2026, 1, 1)
    records = [
        {"employee_id": 7, "attendance_date": day},
        {"employee_id": 7, "attendance_date": day},
        {"employee_id": 8, "attendance_date": day},
        {"employee_id": 7, "attendance_date": date(2026, 1, 2)},
    ]
    groups = duplicate_attendance_groups(records)
    assert len(groups) == 1
    assert {row["employee_id"] for row in groups[0]} == {7}


def test_invalid_time_and_future_date_checks():
    assert invalid_time_range(time(17, 0), time(9, 0))
    assert not invalid_time_range(time(9, 0), time(17, 0))
    today = date(2026, 1, 1)
    assert is_future_date(today + timedelta(days=1), today)
    assert not is_future_date(today, today)
