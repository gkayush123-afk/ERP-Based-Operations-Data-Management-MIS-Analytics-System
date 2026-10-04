import time
from datetime import date

from app.extensions import db
from app.models import AuditLog, Department, Employee, OperationRecord, User
from app.reports.service import build_dashboard_metrics
from app.utils.audit import log_action


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


def add_operation(app, employee_id, department_id, status="verified"):
    with app.app_context():
        record = OperationRecord(
            record_date=date.today(),
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


def setup_dashboard_data(app):
    with app.app_context():
        operations = Department(name="Operations", is_active=True)
        db.session.add(operations)
        db.session.commit()
        operations_id = operations.id
    manager_id = add_user(app, "dash_manager", "manager", operations_id)
    staff_id = add_user(app, "dash_staff", "data_entry", operations_id)
    employee_id = add_employee(app, "DSH001", operations_id)
    return operations_id, manager_id, staff_id, employee_id


def test_operation_cards_scoped_by_role(app):
    operations_id, _manager, _staff, employee_id = setup_dashboard_data(app)
    general_employee = add_employee(app, "DSH002", 1)
    add_operation(app, employee_id, operations_id, "verified")
    add_operation(app, employee_id, operations_id, "pending")
    add_operation(app, employee_id, operations_id, "rejected")
    add_operation(app, general_employee, 1, "verified")

    with app.app_context():
        admin_metrics = build_dashboard_metrics(db.session)
        assert admin_metrics["operation_totals"] == {
            "verified": 2,
            "pending": 1,
            "rejected": 1,
            "needs_correction": 0,
            "total": 4,
        }
        assert admin_metrics["operations_monthly"]["labels"][-1] == date.today().strftime("%b %Y")
        assert sum(admin_metrics["operations_monthly"]["total"]) == 4

        scoped = build_dashboard_metrics(db.session, department_id=operations_id)
        assert scoped["operation_totals"] == {
            "verified": 1,
            "pending": 1,
            "rejected": 1,
            "needs_correction": 0,
            "total": 3,
        }


def test_late_attendance_in_today_counts(app):
    from app.models import AttendanceRecord

    _dept, _manager, staff_id, employee_id = setup_dashboard_data(app)
    with app.app_context():
        db.session.add(
            AttendanceRecord(
                employee_id=employee_id,
                attendance_date=date.today(),
                status="late",
                verification_status="pending",
                created_by=staff_id,
            )
        )
        db.session.commit()
        metrics = build_dashboard_metrics(db.session)
        assert metrics["today_attendance"]["late"] == 1
        assert "late" in metrics["trend_series"]


def test_recent_activities_visibility(app):
    operations_id, _manager, staff_id, _employee = setup_dashboard_data(app)
    with app.app_context():
        staff = db.session.get(User, staff_id)
        admin = db.session.scalar(db.select(User).where(User.username == "admin"))
        log_action(staff, "attendance.create", "attendance_record", 1, {})
        log_action(admin, "user.approve", "user", 2, {})
        db.session.commit()

        admin_feed = build_dashboard_metrics(db.session)["recent_activities"]
        assert len(admin_feed) == 2
        assert admin_feed[0]["actor"] == "Test Administrator"

        scoped_feed = build_dashboard_metrics(db.session, department_id=operations_id)[
            "recent_activities"
        ]
        assert len(scoped_feed) == 1
        assert scoped_feed[0]["actor"] == "Dash Staff"
        assert scoped_feed[0]["label"] == "Marked attendance"


def test_dashboard_renders_new_sections(client, app):
    operations_id, _manager, staff_id, employee_id = setup_dashboard_data(app)
    add_operation(app, employee_id, operations_id, "rejected")
    authenticate_as(client, app, "admin")

    response = client.get("/dashboard")
    assert response.status_code == 200
    for marker in (
        b"total operations",
        b"Operations approved",
        b"Operations awaiting review",
        b"Operations sent back",
        b"Monthly operations",
        b"Recent activities",
        b"operations-chart",
        b"Late",
    ):
        assert marker in response.data
