import time
from datetime import date
from io import BytesIO

from app.extensions import db
from app.models import AttendanceRecord, AuditLog, Department, Employee, User
from app.reports.service import build_report, report_period


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


def add_employee(app, code, department_id):
    with app.app_context():
        employee = Employee(
            employee_code=code,
            full_name=code,
            email=f"{code.lower()}@example.com",
            designation="Analyst",
            department_id=department_id,
            joining_date=date(2020, 1, 1),
            status="active",
        )
        db.session.add(employee)
        db.session.commit()
        return employee.id


def add_attendance(app, employee_id, on_date, status, verification_status="verified"):
    with app.app_context():
        db.session.add(
            AttendanceRecord(
                employee_id=employee_id,
                attendance_date=on_date,
                status=status,
                verification_status=verification_status,
            )
        )
        db.session.commit()


def test_report_periods_cover_selected_day_week_month_and_custom_range():
    selected = date(2026, 10, 3)
    assert report_period("daily", period_date=selected) == (selected, selected)
    assert report_period("weekly", period_date=selected) == (
        date(2026, 9, 28),
        date(2026, 10, 4),
    )
    assert report_period("monthly", period_month=date(2026, 2, 1)) == (
        date(2026, 2, 1),
        date(2026, 2, 28),
    )
    assert report_period(
        "department",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 3),
    ) == (date(2026, 10, 1), date(2026, 10, 3))


def test_aggregated_department_summary_and_status_filter(app):
    operations = Department(name="Report Operations")
    with app.app_context():
        db.session.add(operations)
        db.session.commit()
        operations_id = operations.id
    first_id = add_employee(app, "RPT001", 1)
    second_id = add_employee(app, "RPT002", operations_id)
    on_date = date.today()
    add_attendance(app, first_id, on_date, "present")
    add_attendance(app, second_id, on_date, "absent")

    with app.app_context():
        rows = build_report(
            db.session,
            "department",
            on_date,
            on_date,
        )
        assert rows == [
            {
                "department": "General",
                "present": 1,
                "absent": 0,
                "late": 0,
                "leave": 0,
                "half_day": 0,
                "active_employees": 1,
                "attendance_percentage": 100.0,
                "expected_employee_days": 1,
            },
            {
                "department": "Report Operations",
                "present": 0,
                "absent": 1,
                "late": 0,
                "leave": 0,
                "half_day": 0,
                "active_employees": 1,
                "attendance_percentage": 0.0,
                "expected_employee_days": 1,
            },
        ]
        filtered = build_report(
            db.session,
            "department",
            on_date,
            on_date,
            status="present",
        )
        assert filtered[0]["present"] == 1
        assert filtered[1]["absent"] == 0
        assert filtered[1]["attendance_percentage"] == 0.0


def test_report_access_and_manager_department_scope(client, app):
    operations = Department(name="Scoped Reports")
    with app.app_context():
        db.session.add(operations)
        db.session.commit()
        operations_id = operations.id
    manager_employee = add_employee(app, "SCOPED001", 1)
    other_employee = add_employee(app, "SCOPED002", operations_id)
    on_date = date.today()
    add_attendance(app, manager_employee, on_date, "present")
    add_attendance(app, other_employee, on_date, "absent")

    add_user(app, "report_manager", "manager", 1)
    authenticate_as(client, app, "report_manager")
    response = client.get(f"/reports/?type=daily&date={on_date.isoformat()}&department_id={operations_id}")
    assert response.status_code == 200
    assert b"General" in response.data
    assert b"Scoped Reports" not in response.data
    assert b'name="department_id"' not in response.data

    client.post("/logout")
    add_user(app, "report_entry", "data_entry", 1)
    authenticate_as(client, app, "report_entry")
    assert client.get("/reports/").status_code == 403


def test_excel_and_pdf_exports_are_formatted_and_audited(client, app):
    employee_id = add_employee(app, "EXPORT001", 1)
    on_date = date.today()
    add_attendance(app, employee_id, on_date, "present")
    authenticate_as(client, app, "admin")
    query = f"type=daily&date={on_date.isoformat()}"

    excel = client.get(f"/reports/export/xlsx?{query}")
    assert excel.status_code == 200
    assert excel.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(excel.data), read_only=False)
    sheet = workbook["MIS Report"]
    assert sheet["A3"].value == "Date"
    assert sheet["A4"].value == on_date.isoformat()
    assert sheet.column_dimensions["A"].width >= 12
    assert sheet["A3"].font.bold

    pdf = client.get(f"/reports/export/pdf?{query}")
    assert pdf.status_code == 200
    assert pdf.mimetype == "application/pdf"
    assert pdf.data.startswith(b"%PDF")

    with app.app_context():
        exports = db.session.scalars(
            db.select(AuditLog).where(AuditLog.action == "reports.export")
        ).all()
        assert len(exports) == 2
        assert {event.details["format"] for event in exports} == {"xlsx", "pdf"}


def test_dashboard_shows_aggregated_metrics_and_charts(client, app):
    employee_id = add_employee(app, "DASH001", 1)
    on_date = date.today()
    add_attendance(app, employee_id, on_date, "present", verification_status="pending")
    authenticate_as(client, app, "admin")

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert b"1 active employees" in response.data
    assert b"1 attendance entries" in response.data
    assert b"1 pending verifications" in response.data
    assert b"department-chart" in response.data
    assert b"monthly-chart" in response.data


def test_dashboard_renders_when_optional_operational_metrics_are_missing(
    client, app, monkeypatch
):
    legacy_metrics = {
        "total_employees": 0,
        "today_attendance": {"present": 0},
        "today_attendance_total": 0,
        "pending_verifications": 0,
        "department_labels": [],
        "department_series": {},
        "trend_labels": [],
        "trend_series": {},
    }
    monkeypatch.setattr(
        "app.main.routes.build_dashboard_metrics",
        lambda _session, department_id=None: legacy_metrics,
    )
    authenticate_as(client, app, "admin")

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert b"Not available" in response.data


def test_unassigned_non_admin_cannot_view_unscoped_dashboard(client, app):
    add_user(app, "unassigned_entry", "data_entry", None)
    authenticate_as(client, app, "unassigned_entry")
    assert client.get("/dashboard").status_code == 403
