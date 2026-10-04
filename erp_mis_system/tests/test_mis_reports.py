import time
from datetime import date, timedelta

from app.extensions import db
from app.models import AuditLog, Department, Employee, OperationRecord, User
from app.reports.service import (
    build_operation_by_department,
    build_operation_summary,
    pct_change,
    previous_period,
)


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
            full_name=username.replace("_", " ").title(),
            username=username,
            email=f"{username}@example.com",
            department_id=department_id,
            role=role,
            status="active",
        )
        user.set_password("Test-Pass-1234!")
        db.session.add(user)
        db.session.commit()
        return user.id


def add_department(app, name):
    with app.app_context():
        department = Department(name=name, is_active=True)
        db.session.add(department)
        db.session.commit()
        return department.id


def add_employee(app, code, department_id):
    with app.app_context():
        employee = Employee(
            employee_code=code,
            full_name=f"Employee {code}",
            email=f"{code.lower()}@example.com",
            designation="Analyst",
            department_id=department_id,
            joining_date=date(2020, 1, 1),
            status="active",
        )
        db.session.add(employee)
        db.session.commit()
        return employee.id


def add_record(app, employee_id, department_id, status="verified", day_offset=0):
    with app.app_context():
        record = OperationRecord(
            record_date=date.today() - timedelta(days=day_offset),
            employee_id=employee_id,
            department_id=department_id,
            task_operation="Order processing",
            total_records=10,
            completed=10 if status == "verified" else 0,
            pending=0,
            status=status,
        )
        db.session.add(record)
        db.session.commit()
        return record.id


def setup_mis_data(app):
    operations_id = add_department(app, "Operations")
    manager_id = add_user(app, "mis_manager", "manager", operations_id)
    staff_id = add_user(app, "mis_staff", "data_entry", operations_id)
    employee_id = add_employee(app, "MIS001", operations_id)
    return operations_id, manager_id, staff_id, employee_id


def test_access_control(client, app):
    _dept, _manager, _staff, _employee = setup_mis_data(app)
    authenticate_as(client, app, "mis_staff")
    assert client.get("/reports/operations").status_code == 403
    assert client.get("/reports/operations/export/xlsx").status_code == 403
    client.post("/logout")
    response = client.get("/reports/operations")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_page_renders_cards_charts_and_table(client, app):
    operations_id, _manager, staff_id, employee_id = setup_mis_data(app)
    add_record(app, employee_id, operations_id, "verified")
    add_record(app, employee_id, operations_id, "pending")
    authenticate_as(client, app, "mis_manager")

    response = client.get("/reports/operations")
    assert response.status_code == 200
    for marker in (
        b"MIS Reports",
        b"Total Records",
        b"Verified Records",
        b"Pending Records",
        b"Rejected Records",
        b"mis-line-chart",
        b"mis-donut-chart",
        b"mis-bar-chart",
        b"Department-wise Detailed Report",
        b"Completion %",
        b"Export Excel",
        b"Export PDF",
        b"chart.umd.min.js",
    ):
        assert marker in response.data


def test_verified_only_numbers_and_trends(client, app):
    operations_id, _manager, _staff, employee_id = setup_mis_data(app)
    add_record(app, employee_id, operations_id, "verified")
    add_record(app, employee_id, operations_id, "verified")
    add_record(app, employee_id, operations_id, "pending")
    add_record(app, employee_id, operations_id, "rejected")
    today = date.today()
    month_start = today.replace(day=1)

    with app.app_context():
        summary = build_operation_summary(db.session, month_start, today)
        assert summary == {"total": 4, "verified": 2, "pending": 1, "rejected": 1, "needs_correction": 0}
        assert pct_change(4, 0) == (100.0, "up")
        assert pct_change(0, 0) == (0.0, "flat")
        assert pct_change(1, 4) == (-75.0, "down")
        prev_start, prev_end = previous_period(month_start, today)
        assert (prev_end - prev_start).days == (today - month_start).days
        assert prev_end == month_start - timedelta(days=1)
        dept_rows = build_operation_by_department(db.session, month_start, today)
        assert dept_rows[0]["completion_pct"] == 50.0
        assert dept_rows[0]["total"] == 4

    authenticate_as(client, app, "mis_manager")
    response = client.get("/reports/operations")
    assert b">4<" in response.data  # total card value


def test_filters_and_validation(client, app):
    operations_id, _manager, _staff, employee_id = setup_mis_data(app)
    add_record(app, employee_id, operations_id, "verified")
    authenticate_as(client, app, "admin")
    assert client.get("/reports/operations", query_string={"department_id": 9999}).status_code == 400
    authenticate_as(client, app, "mis_manager")

    assert client.get("/reports/operations", query_string={"type": "bogus"}).status_code == 400
    assert client.get("/reports/operations", query_string={"start_date": "bad"}).status_code == 400
    assert (
        client.get(
            "/reports/operations",
            query_string={"start_date": "2020-01-01", "end_date": "2022-01-01"},
        ).status_code
        == 400
    )

    response = client.get("/reports/operations", query_string={"type": "employee"})
    assert b"Employee-wise Detailed Report" in response.data
    assert b"MIS001" in response.data

    response = client.get("/reports/operations", query_string={"q": "zzz-no-match"})
    assert b"No records found." in response.data

    other_id = add_employee(app, "MIS002", operations_id)
    response = client.get("/reports/operations", query_string={"employee_id": other_id})
    assert b"No records found." in response.data

    finance_id = add_department(app, "Finance")
    assert (
        client.get("/reports/operations", query_string={"department_id": finance_id}).status_code
        == 200
    )


def test_donut_others_and_view_all(client, app):
    _dept, _manager, _staff, _employee = setup_mis_data(app)
    with app.app_context():
        for index in range(11):
            department = Department(name=f"Dept{index:02d}", is_active=True)
            db.session.add(department)
            db.session.flush()
            employee = Employee(
                employee_code=f"DON{index:02d}",
                full_name=f"Don {index}",
                email=f"don{index}@example.com",
                designation="Analyst",
                department_id=department.id,
                joining_date=date(2020, 1, 1),
                status="active",
            )
            db.session.add(employee)
            db.session.flush()
            db.session.add(
                OperationRecord(
                    record_date=date.today(),
                    employee_id=employee.id,
                    department_id=department.id,
                    task_operation="Task",
                    total_records=5,
                    completed=5,
                    pending=0,
                    status="verified",
                )
            )
        db.session.commit()
    authenticate_as(client, app, "admin")

    response = client.get("/reports/operations")
    assert b"Others" in response.data
    assert b"View All Reports" in response.data

    response = client.get("/reports/operations", query_string={"full": "1"})
    assert response.status_code == 200


def test_exports_respect_filters_and_audit(client, app):
    operations_id, _manager, staff_id, employee_id = setup_mis_data(app)
    add_record(app, employee_id, operations_id, "verified")
    authenticate_as(client, app, "mis_manager")

    response = client.get("/reports/operations/export/xlsx")
    assert response.status_code == 200
    assert "spreadsheetml.sheet" in response.headers["Content-Type"]
    assert "attachment" in response.headers["Content-Disposition"]

    response = client.get("/reports/operations/export/pdf")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/pdf"
    assert response.data.startswith(b"%PDF")

    assert client.get("/reports/operations/export/doc").status_code == 404
    with app.app_context():
        audits = db.session.scalars(
            db.select(AuditLog).where(AuditLog.action == "mis_reports.export")
        ).all()
        assert len(audits) == 2
        assert audits[0].details["generated_by"]


def test_manager_scoped_to_own_department(client, app):
    operations_id, _manager, _staff, employee_id = setup_mis_data(app)
    finance_id = add_department(app, "Finance")
    other_id = add_employee(app, "MIS009", finance_id)
    add_record(app, employee_id, operations_id, "verified")
    add_record(app, other_id, finance_id, "verified")
    authenticate_as(client, app, "mis_manager")

    response = client.get("/reports/operations")
    assert b"Finance" not in response.data
    assert (
        client.get(
            "/reports/operations/export/xlsx", query_string={"employee_id": other_id}
        ).status_code
        == 403
    )
