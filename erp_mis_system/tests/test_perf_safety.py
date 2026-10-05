"""Post-fix functional safety tests: behaviour must be unchanged, only faster.

Covers login/logout, role access, employee CRUD, verification
approve/reject, attendance marking, report filters + exports, CSRF
enforcement, 404 handling, health routes, and query-count guards that
lock in the N+1 fixes.
"""

import io
import time
from datetime import date, timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models import AttendanceRecord, Department, Employee, OperationRecord, User
from sqlalchemy import event


@pytest.fixture()
def perf_app():
    class SafetyConfig:
        SECRET_KEY = "safety-test-key"
        SQLALCHEMY_DATABASE_URI = "sqlite://"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = False
        TESTING = True
        RATELIMIT_LOGIN = "1000 per minute"
        RATELIMIT_REGISTER = "1000 per minute"

    app = create_app(SafetyConfig)
    with app.app_context():
        db.create_all()
        dept = Department(name="SafetyDept")
        db.session.add(dept)
        db.session.flush()
        users = {}
        for username, role in (("safety_admin", "admin"), ("safety_mgr", "manager"), ("safety_de", "data_entry")):
            user = User(
                full_name=username.replace("_", " ").title(),
                username=username,
                email=f"{username}@example.test",
                department_id=dept.id,
                role=role,
                status="active",
            )
            user.set_password("Test-Pass-1234!")
            db.session.add(user)
            db.session.flush()
            users[role] = user.id
        dept_id = dept.id
        employees = []
        for i in range(5):
            employee = Employee(
                employee_code=f"SAFE-{i:03d}",
                full_name=f"Safe Employee {i}",
                email=f"safe{i}@example.test",
                designation="Analyst",
                department_id=dept_id,
                joining_date=date.today() - timedelta(days=100),
                status="active",
                created_by=users["admin"],
            )
            db.session.add(employee)
            db.session.flush()
            employees.append(employee.id)
        for eid in employees[:3]:
            db.session.add(
                OperationRecord(
                    record_date=date.today(),
                    employee_id=eid,
                    department_id=dept_id,
                    task_operation="Safety batch",
                    total_records=10,
                    completed=6,
                    pending=4,
                    status="pending",
                    submitted_by=users["data_entry"],
                )
            )
            db.session.add(
                AttendanceRecord(
                    employee_id=eid,
                    attendance_date=date.today(),
                    status="present",
                    verification_status="pending",
                    created_by=users["data_entry"],
                )
            )
        db.session.commit()
    yield app, {"users": users, "dept_id": dept_id, "employees": employees}
    with app.app_context():
        db.session.remove()
        db.drop_all()


def login_as(client, perf_app, role):
    app, ids = perf_app
    with app.app_context():
        user_id = ids["users"][role]
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["_permanent"] = True
        session["last_activity"] = time.time()


def query_counter():
    state = {"count": 0, "employee_by_id": 0, "stmts": []}

    def before(conn, cursor, statement, parameters, context, executemany):
        pass

    def after(conn, cursor, statement, parameters, context, executemany):
        state["count"] += 1
        stmt = str(statement)
        state["stmts"].append(stmt[:150])
        if "FROM employees" in stmt and "employees.id = ?" in stmt:
            state["employee_by_id"] += 1

    event.listen(db.engine, "before_cursor_execute", before)
    event.listen(db.engine, "after_cursor_execute", after)
    try:
        yield state
    finally:
        event.remove(db.engine, "after_cursor_execute", after)
        event.remove(db.engine, "before_cursor_execute", before)


@pytest.fixture()
def count_queries(perf_app):
    app, _ids = perf_app
    with app.app_context():
        yield from query_counter()


def test_login_logout_flow(perf_app):
    app, _ids = perf_app
    client = app.test_client()
    response = client.post(
        "/login",
        data={"username": "safety_admin", "password": "Test-Pass-1234!"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    response = client.post("/logout", follow_redirects=True)
    assert response.status_code == 200
    assert client.get("/dashboard").status_code == 302


def test_role_access_matrix(perf_app):
    app, _ids = perf_app
    admin_pages = ["/admin/users/", "/reports/operations", "/verification/", "/data-quality/"]
    for page in admin_pages:
        client = app.test_client()
        login_as(client, perf_app, "data_entry")
        assert client.get(page).status_code == 403, page
    for page in ["/reports/operations", "/verification/"]:
        client = app.test_client()
        login_as(client, perf_app, "manager")
        assert client.get(page).status_code == 200, page
    for page in ["/employees/", "/attendance/", "/dashboard", "/settings", "/roles"]:
        client = app.test_client()
        login_as(client, perf_app, "data_entry")
        assert client.get(page).status_code == 200, page


def test_employee_crud(perf_app):
    app, ids = perf_app
    client = app.test_client()
    login_as(client, perf_app, "admin")
    response = client.post(
        "/employees/create",
        data={
            "employee_code": "SAFE-NEW",
            "full_name": "Safe Newcomer",
            "email": "newcomer@example.com",
            "phone": "",
            "designation": "Clerk",
            "department_id": str(ids["dept_id"]),
            "joining_date": "2024-02-01",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"SAFE-NEW" in response.data
    with app.app_context():
        employee_id = db.session.scalar(
            db.select(Employee.id).where(Employee.employee_code == "SAFE-NEW")
        )
    assert client.get(f"/employees/{employee_id}").status_code == 200
    response = client.post(
        f"/employees/{employee_id}/edit",
        data={
            "employee_code": "SAFE-NEW",
            "full_name": "Safe Renamed",
            "email": "newcomer@example.com",
            "phone": "",
            "designation": "Clerk",
            "department_id": str(ids["dept_id"]),
            "joining_date": "2024-02-01",
            "status": "inactive",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    response = client.post(f"/employees/{employee_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert b"was deleted" in response.data


def test_verification_approve_and_reject(perf_app):
    app, _ids = perf_app
    client = app.test_client()
    login_as(client, perf_app, "manager")
    with app.app_context():
        pending = db.session.scalars(
            db.select(OperationRecord.id)
            .where(OperationRecord.status == "pending")
            .order_by(OperationRecord.id)
        ).all()
    assert len(pending) >= 2
    assert client.post(f"/verification/{pending[0]}/approve", follow_redirects=True).status_code == 200
    with app.app_context():
        assert db.session.get(OperationRecord, pending[0]).status == "verified"
    response = client.post(
        f"/verification/{pending[1]}/reject",
        data={"rejection_reason": "Safety test rejection"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        rejected = db.session.get(OperationRecord, pending[1])
        assert rejected.status == "rejected"
        assert rejected.rejection_reason == "Safety test rejection"


def test_attendance_mark_and_report_exports(perf_app):
    app, ids = perf_app
    client = app.test_client()
    login_as(client, perf_app, "data_entry")
    response = client.post(
        "/attendance/mark",
        data={
            "mark_date": date.today().isoformat(),
            "employee_id": [str(ids["employees"][4])],
            "status": ["present"],
            "in_time": ["09:00"],
            "out_time": ["17:00"],
            "remarks": [""],
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert (
            db.session.scalar(
                db.select(AttendanceRecord.id).where(
                    AttendanceRecord.employee_id == ids["employees"][4],
                    AttendanceRecord.attendance_date == date.today(),
                )
            )
            is not None
        )
    login_as(client, perf_app, "manager")
    response = client.get("/reports/operations?type=employee&full=1")
    assert response.status_code == 200
    assert b"SAFE-000" in response.data
    xlsx = client.get("/reports/operations/export/xlsx")
    assert xlsx.status_code == 200
    assert xlsx.data[:2] == b"PK"
    assert len(xlsx.data) > 1000
    pdf = client.get("/reports/operations/export/pdf")
    assert pdf.status_code == 200
    assert pdf.data[:4] == b"%PDF"


def test_csrf_enforced_when_enabled():
    class CsrfConfig:
        SECRET_KEY = "csrf-test-key"
        SQLALCHEMY_DATABASE_URI = "sqlite://"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = True
        # TESTING must stay False: Flask-WTF skips CSRF validation when
        # app.testing is True, which would defeat this test.
        TESTING = False
        RATELIMIT_LOGIN = "1000 per minute"
        RATELIMIT_REGISTER = "1000 per minute"

    app = create_app(CsrfConfig)
    with app.app_context():
        db.create_all()
    try:
        client = app.test_client()
        response = client.post("/login", data={"username": "x", "password": "y"})
        assert response.status_code == 400
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()


def test_404_and_health_routes(perf_app):
    app, _ids = perf_app
    client = app.test_client()
    assert client.get("/no-such-page").status_code == 404
    assert client.get("/health").status_code == 200
    assert client.get("/health").get_json() == {"status": "ok"}
    assert client.get("/health/db").status_code == 200


def test_dashboard_query_budget(perf_app, count_queries):
    app, _ids = perf_app
    client = app.test_client()
    login_as(client, perf_app, "admin")
    assert client.get("/dashboard").status_code == 200
    # Was 19 queries with a 6x repeated monthly aggregate; single grouped
    # query must replace the loop.
    monthly_repeats = sum(1 for s in count_queries["stmts"] if "record_date >= ?" in s and "GROUP BY" in s)
    assert count_queries["count"] <= 14, count_queries["count"]
    assert monthly_repeats <= 2, monthly_repeats


def test_verification_no_per_row_employee_queries(perf_app, count_queries):
    app, _ids = perf_app
    client = app.test_client()
    login_as(client, perf_app, "manager")
    assert client.get("/verification/").status_code == 200
    # Eager loading: no SELECT ... WHERE employees.id = ? per row.
    assert count_queries["employee_by_id"] == 0, count_queries["stmts"]
