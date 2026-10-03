import time

import pytest

from app.extensions import db
from app.models import User


ROLE_ROUTES = [
    "/workspace/records/add",
    "/workspace/records/edit-own",
    "/workspace/department-data",
    "/workspace/records/verify",
    "/workspace/reports",
    "/workspace/reports/export",
    "/workspace/records/delete",
    "/workspace/users",
    "/workspace/audit",
]

MANAGER_ROUTES = {
    "/workspace/records/verify",
    "/workspace/reports",
    "/workspace/reports/export",
}

ADMIN_ROUTES = {
    "/workspace/records/delete",
    "/workspace/users",
    "/workspace/audit",
}


@pytest.fixture()
def role_accounts(app):
    with app.app_context():
        department_id = db.session.scalar(db.select(User.department_id).limit(1))
        accounts = {}
        for role in ("admin", "manager", "data_entry"):
            user = db.session.scalar(db.select(User).where(User.role == role))
            if user is None:
                user = User(
                    full_name=f"Test {role}",
                    username=f"test_{role}",
                    email=f"test_{role}@example.test",
                    department_id=department_id,
                    role=role,
                    status="active",
                )
                user.set_password("Test-Pass-1234!")
                db.session.add(user)
            user.set_password("Test-Pass-1234!")
            accounts[role] = user.username
        db.session.commit()
        return accounts


def login_as(client, app, username):
    with app.app_context():
        user_id = db.session.scalar(
            db.select(User.id).where(User.username == username)
        )
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["_permanent"] = True
        session["last_activity"] = time.time()


@pytest.mark.parametrize("path", ROLE_ROUTES)
def test_admin_can_access_all_role_routes(client, app, role_accounts, path):
    login_as(client, app, role_accounts["admin"])
    response = client.get(path)
    expected_status = 302 if path in {
        "/workspace/users",
        "/workspace/audit",
        "/workspace/records/verify",
        "/workspace/reports",
        "/workspace/reports/export",
    } else 200
    assert response.status_code == expected_status


@pytest.mark.parametrize("path", ROLE_ROUTES)
def test_manager_can_access_manager_and_general_routes(client, app, role_accounts, path):
    login_as(client, app, role_accounts["manager"])
    response = client.get(path)
    expected_status = 302 if path in {
        "/workspace/records/verify",
        "/workspace/reports",
        "/workspace/reports/export",
    } else 403 if path in ADMIN_ROUTES else 200
    assert response.status_code == expected_status
    if expected_status == 403:
        assert b"Access denied" in response.data


@pytest.mark.parametrize("path", ROLE_ROUTES)
def test_data_entry_can_access_only_general_routes(client, app, role_accounts, path):
    login_as(client, app, role_accounts["data_entry"])
    response = client.get(path)
    expected_status = 403 if path in MANAGER_ROUTES | ADMIN_ROUTES else 200
    assert response.status_code == expected_status
    if expected_status == 403:
        assert b"Access denied" in response.data


def test_anonymous_user_is_redirected_to_login(client):
    response = client.get("/workspace/reports")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?")


def test_dashboard_menu_is_role_aware(client, app, role_accounts):
    login_as(client, app, role_accounts["manager"])
    manager_page = client.get("/dashboard")
    assert b"Reports" in manager_page.data
    assert b"Reports &amp; exports" in manager_page.data
    assert b"Audit logs" not in manager_page.data
    assert b"Delete records" not in manager_page.data

    client.post("/logout")
    login_as(client, app, role_accounts["data_entry"])
    data_entry_page = client.get("/dashboard")
    assert b"Department data" in data_entry_page.data
    assert b"Reports" not in data_entry_page.data
    assert b"Reports &amp; exports" not in data_entry_page.data
    assert b"Audit logs" not in data_entry_page.data
