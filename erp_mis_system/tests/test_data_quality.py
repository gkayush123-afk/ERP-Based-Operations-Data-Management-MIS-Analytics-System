import time
from datetime import date, timedelta

import pytest

from app.data_quality.service import build_data_quality_report, summarize_data_quality
from app.extensions import db
from app.models import AttendanceRecord, Department, Employee, User


def authenticate_as(client, app, username):
    with app.app_context():
        user_id = db.session.scalar(db.select(User.id).where(User.username == username))
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["_permanent"] = True
        session["last_activity"] = time.time()


def add_user(app, username, role, department_id):
    with app.app_context():
        user = User(
            full_name=username,
            username=username,
            email=f"{username}@example.com",
            department_id=department_id,
            role=role,
            status="active",
        )
        user.set_password("Test-Pass-1234!")
        db.session.add(user)
        db.session.commit()


def add_employee(app, code, department_id, *, email=None, joining_date=None, full_name=None):
    with app.app_context():
        employee = Employee(
            employee_code=code,
            full_name=full_name if full_name is not None else f"Employee {code}",
            email=email or f"{code.lower()}@example.com",
            designation="Analyst",
            department_id=department_id,
            joining_date=joining_date or date(2020, 1, 1),
            status="active",
        )
        db.session.add(employee)
        db.session.commit()
        return employee.id


def test_report_detects_missing_duplicate_email_future_and_missing_attendance(app):
    today = date.today()
    tomorrow = today + timedelta(days=1)
    first_id = add_employee(
        app,
        "DQ001",
        1,
        email="same@example.com",
        joining_date=tomorrow,
        full_name=" ",
    )
    second_id = add_employee(app, "DQ002", 1, email=" SAME@example.com ")
    with app.app_context():
        employee = db.session.get(Employee, second_id)
        employee.designation = ""
        record = AttendanceRecord(
            employee_id=first_id,
            attendance_date=tomorrow,
            status="present",
            in_time=None,
            out_time=None,
            verification_status="pending",
            created_by=None,
        )
        db.session.add(record)
        db.session.commit()

        report = build_data_quality_report(
            db.session,
            start_date=today,
            end_date=today,
            department_id=1,
            today=today,
        )

        assert any(issue["code"] == "employee_missing_field" for issue in report["missing"])
        assert any(issue["code"] == "employee_missing_attendance" for issue in report["missing"])
        assert any(issue["code"] == "attendance_missing_field" for issue in report["missing"])
        assert any(issue["code"] == "duplicate_employee_email" for issue in report["duplicates"])
        assert any(issue["code"] == "employee_future_joining_date" for issue in report["invalid"])
        assert any(issue["code"] == "attendance_future_date" for issue in report["invalid"])
        assert summarize_data_quality(report)["total"] > 0


def test_data_quality_route_requires_manager_or_admin(client, app):
    response = client.get("/data-quality/")
    assert response.status_code == 302

    add_user(app, "quality_entry", "data_entry", 1)
    authenticate_as(client, app, "quality_entry")
    response = client.get("/data-quality/")
    assert response.status_code == 403
    assert b"Access denied" in response.data


def test_manager_quality_page_is_limited_to_own_department(client, app):
    operations = Department(name="Quality Operations")
    with app.app_context():
        db.session.add(operations)
        db.session.commit()
        operations_id = operations.id
    add_user(app, "quality_manager", "manager", 1)
    add_employee(app, "QUALITY-GEN", 1, full_name=" ")
    add_employee(app, "QUALITY-OPS", operations_id, full_name=" ")

    authenticate_as(client, app, "quality_manager")
    response = client.get("/data-quality/")
    assert response.status_code == 200
    assert b"QUALITY-GEN" in response.data
    assert b"QUALITY-OPS" not in response.data
    assert b'name="department_id"' not in response.data


def test_quality_filters_and_edit_link(client, app):
    authenticate_as(client, app, "admin")
    add_employee(app, "QUALITY-FILTER", 1, full_name=" ")
    response = client.get(
        f"/data-quality/?start_date={date.today().isoformat()}&"
        f"end_date={date.today().isoformat()}&issue=missing"
    )
    assert response.status_code == 200
    assert b"QUALITY-FILTER" in response.data
    assert b"/employees/" in response.data
    assert b"Fix / edit" in response.data
