import time

import pytest

from app.extensions import db
from app.models import AuditLog, User

TEMP_PASSWORD = "Temporary-Pass-1234!"
NEW_PASSWORD = "Changed-Pass-1234!"


def set_logged_in_user(client, app, username="admin"):
    with app.app_context():
        user_id = db.session.scalar(db.select(User.id).where(User.username == username))
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["_permanent"] = True
        session["last_activity"] = time.time()


def create_user(client, username="created_user"):
    return client.post(
        "/admin/users/create",
        data={
            "username": username,
            "full_name": "Created User",
            "email": f"{username}@example.com",
            "role": "data_entry",
            "department_id": "1",
            "temporary_password": TEMP_PASSWORD,
        },
        follow_redirects=True,
    )


def audit_actions():
    return db.session.scalars(db.select(AuditLog.action)).all()


def test_admin_user_management_create_edit_disable_reset_and_audit(client, app):
    set_logged_in_user(client, app)

    response = client.get("/admin/users/")
    assert response.status_code == 200
    assert b"Pending registrations" in response.data

    response = create_user(client)
    assert response.status_code == 200
    assert b"User created_user was created" in response.data

    with app.app_context():
        created = db.session.scalar(db.select(User).where(User.username == "created_user"))
        assert created.status == "active"
        assert created.role == "data_entry"
        assert created.must_change_password is True
        assert created.check_password(TEMP_PASSWORD)
        assert "user.create" in audit_actions()
        create_event = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "user.create")
        )
        assert "temporary_password" not in str(create_event.details).lower()
        created_id = created.id

    response = client.post(
        f"/admin/users/{created_id}/edit",
        data={"role": "manager", "department_id": "1"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        created = db.session.get(User, created_id)
        assert created.role == "manager"
        assert "user.update" in audit_actions()

    response = client.post(
        f"/admin/users/{created_id}/status",
        data={"status": "disabled"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        created = db.session.get(User, created_id)
        assert created.status == "disabled"
        assert "user.disabled" in audit_actions()

    response = client.post(
        f"/admin/users/{created_id}/status",
        data={"status": "active"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    response = client.post(
        f"/admin/users/{created_id}/reset-password",
        data={"temporary_password": "Reset-Temporary-1234!"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        created = db.session.get(User, created_id)
        assert created.must_change_password is True
        assert created.check_password("Reset-Temporary-1234!")
        assert "user.password_reset" in audit_actions()

    audit_page = client.get("/admin/users/audit-logs")
    assert audit_page.status_code == 200
    assert b"user.password_reset" in audit_page.data


def test_created_user_must_change_temporary_password_and_action_is_audited(client, app):
    set_logged_in_user(client, app)
    create_user(client)
    client.post("/logout")

    login_response = client.post(
        "/login",
        data={"username": "created_user", "password": TEMP_PASSWORD},
    )
    assert login_response.status_code == 302
    assert login_response.headers["Location"].endswith("/change-password")

    response = client.get("/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/change-password")

    response = client.post(
        "/change-password",
        data={
            "current_password": TEMP_PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Your password was changed successfully" in response.data
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "created_user"))
        assert user.must_change_password is False
        assert user.check_password(NEW_PASSWORD)
        assert "user.password_changed" in audit_actions()


def test_admin_cannot_demote_or_deactivate_self(client, app):
    set_logged_in_user(client, app, "admin")
    with app.app_context():
        admin_id = db.session.scalar(db.select(User.id).where(User.username == "admin"))

    response = client.post(
        f"/admin/users/{admin_id}/edit",
        data={"role": "manager", "department_id": "1"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"cannot demote your own administrator account" in response.data

    response = client.post(
        f"/admin/users/{admin_id}/status",
        data={"status": "disabled"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"cannot deactivate your own administrator account" in response.data

    with app.app_context():
        admin = db.session.get(User, admin_id)
        assert admin.role == "admin"
        assert admin.status == "active"


@pytest.mark.parametrize(
    ("role", "path"),
    [
        ("manager", "/admin/users/"),
        ("data_entry", "/admin/users/"),
        ("manager", "/admin/users/audit-logs"),
        ("data_entry", "/admin/users/audit-logs"),
    ],
)
def test_non_admin_cannot_open_admin_user_management(client, app, role, path):
    with app.app_context():
        user = User(
            full_name=f"Test {role}",
            username=f"role_{role}",
            email=f"role_{role}@example.com",
            department_id=1,
            role=role,
            status="active",
        )
        user.set_password("Test-Pass-1234!")
        db.session.add(user)
        db.session.commit()
    set_logged_in_user(client, app, f"role_{role}")

    response = client.get(path)
    assert response.status_code == 403
    assert b"Access denied" in response.data


def test_user_list_search_and_filters(client, app):
    set_logged_in_user(client, app)
    create_user(client, username="filtered_user")

    response = client.get("/admin/users/?q=filtered&role=data_entry&status=active")
    assert response.status_code == 200
    assert b"filtered_user" in response.data

    response = client.get("/admin/users/?q=filtered&role=manager&status=active")
    assert response.status_code == 200
    assert b"filtered_user" not in response.data
