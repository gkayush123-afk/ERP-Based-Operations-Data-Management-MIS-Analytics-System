import time
from datetime import date, datetime
from io import BytesIO
from logging.handlers import RotatingFileHandler
from pathlib import Path

from openpyxl import load_workbook

from app.extensions import db
from app.models import AuditLog, User


def authenticate_as(client, app, username):
    with app.app_context():
        user_id = db.session.scalar(db.select(User.id).where(User.username == username))
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["_permanent"] = True
        session["last_activity"] = time.time()


def add_user(app, username, role, department_id=1):
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
        return user.id


def add_event(app, user_id, action, event_date, ip_address="192.0.2.5"):
    with app.app_context():
        db.session.add(
            AuditLog(
                actor_user_id=user_id,
                action=action,
                entity="test",
                entity_id=1,
                details={"sample": "event"},
                occurred_at=datetime.combine(event_date, datetime.min.time()),
                ip_address=ip_address,
            )
        )
        db.session.commit()


def test_audit_page_is_admin_only_and_supports_all_filters(client, app):
    manager_id = add_user(app, "audit_manager", "manager")
    add_event(app, 1, "auth.login", date(2026, 10, 1))
    add_event(app, manager_id, "employee.create", date(2026, 10, 2))
    add_event(app, manager_id, "employee.update", date(2026, 10, 3))

    add_user(app, "audit_entry", "data_entry")
    authenticate_as(client, app, "audit_entry")
    assert client.get("/admin/users/audit-logs").status_code == 403
    client.post("/logout")

    authenticate_as(client, app, "admin")
    response = client.get(
        "/admin/users/audit-logs",
        query_string={
            "user_id": str(manager_id),
            "action": "employee.create",
            "start_date": "2026-10-02",
            "end_date": "2026-10-02",
        },
    )
    assert response.status_code == 200
    assert b"audit_manager" in response.data
    assert b"employee.create" in response.data
    assert b"2026-10-03 00:00:00" not in response.data
    assert b"192.0.2.5" in response.data
    assert b'name="user_id"' in response.data


def test_authentication_events_include_ip_and_record_no_password(client, app):
    client.post("/login", data={"username": "admin", "password": "incorrect"})
    with app.app_context():
        failure = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "auth.login_failed")
        )
        assert failure is not None
        assert failure.actor_user_id is not None
        assert failure.ip_address == "127.0.0.1"
        assert "username" in failure.details
        assert "password" not in str(failure.details).lower()

    client.post("/login", data={"username": "unknown_audit_user", "password": "incorrect"})
    with app.app_context():
        unknown_failure = db.session.scalar(
            db.select(AuditLog).where(
                AuditLog.action == "auth.login_failed",
                AuditLog.actor_user_id.is_(None),
            )
        )
        assert unknown_failure.details["username"] == "unknown_audit_user"
        assert unknown_failure.ip_address == "127.0.0.1"

    client.post("/login", data={"username": "admin", "password": "Admin-Pass-1234!"})
    client.post("/logout")
    with app.app_context():
        actions = set(
            db.session.scalars(
                db.select(AuditLog.action).where(
                    AuditLog.action.in_(("auth.login", "auth.logout"))
                )
            ).all()
        )
        assert actions == {"auth.login", "auth.logout"}


def test_audit_excel_export_contains_filtered_rows_and_audit_event(client, app):
    add_event(app, 1, "employee.create", date.today())
    add_event(app, 1, "employee.update", date.today())
    authenticate_as(client, app, "admin")

    response = client.get(
        "/admin/users/audit-logs/export",
        query_string={"action": "employee.create", "start_date": date.today().isoformat()},
    )
    assert response.status_code == 200
    assert response.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    sheet = load_workbook(BytesIO(response.data), read_only=False)["Audit Logs"]
    assert sheet["A1"].value == "Time (UTC)"
    assert sheet["C2"].value == "employee.create"
    assert sheet["F2"].value == "192.0.2.5"
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == "A1:G2"
    assert sheet.column_dimensions["G"].width == 60
    assert sheet["A1"].font.bold
    with app.app_context():
        export_log = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "audit.export")
        )
        assert export_log is not None
        assert export_log.ip_address == "127.0.0.1"


def test_invalid_audit_filter_dates_return_bad_request(client, app):
    authenticate_as(client, app, "admin")
    assert client.get(
        "/admin/users/audit-logs",
        query_string={"start_date": "2026-10-03", "end_date": "2026-10-02"},
    ).status_code == 400


def test_csrf_and_production_debug_setting(app, client):
    assert app.config["DEBUG"] is False
    log_file = Path(app.instance_path) / "logs" / "erp_mis.log"
    assert log_file.exists()
    assert any(
        isinstance(handler, RotatingFileHandler)
        for handler in app.logger.handlers
    )
    app.config["WTF_CSRF_ENABLED"] = True
    response = client.post("/login", data={"username": "admin", "password": "bad"})
    assert response.status_code == 400
