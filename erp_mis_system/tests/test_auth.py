from app.extensions import db
from app.models import AuditLog, User
from app.config import DevelopmentConfig, ProductionConfig


def register(client, username="newuser", email="newuser@example.com", role="data_entry"):
    return client.post(
        "/register",
        data={
            "full_name": "New User",
            "username": username,
            "email": email,
            "department_id": "1",
            "role": role,
            "password": "Strong-Pass-123",
            "confirm_password": "Strong-Pass-123",
        },
        follow_redirects=True,
    )


def login(client, username, password):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_landing_and_registration_waits_for_approval(client, app):
    assert client.get("/").status_code == 200

    response = register(client)
    assert b"Your account is waiting for admin approval" in response.data

    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "newuser"))
        assert user is not None
        assert user.status == "pending"
        assert user.role == "data_entry"
        assert user.password_hash != "Strong-Pass-123"
        registration_event = db.session.scalar(
            db.select(AuditLog).where(
                AuditLog.action == "user.register",
                AuditLog.actor_user_id == user.id,
            )
        )
        assert registration_event is not None
        assert registration_event.ip_address == "127.0.0.1"

    response = login(client, "newuser", "Strong-Pass-123")
    assert b"Your account is waiting for admin approval." in response.data
    assert b"Welcome, New User" not in response.data


def test_admin_can_approve_registration_and_user_can_log_in(client, app):
    register(client)
    login(client, "admin", "Admin-Pass-1234!")
    response = client.post("/admin/approvals/2/approve", follow_redirects=True)
    assert response.status_code == 200
    assert b"was approved" in response.data

    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "newuser"))
        assert user.status == "active"
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "user.approve")
        ) is not None

    client.post("/logout", follow_redirects=True)
    response = login(client, "newuser", "Strong-Pass-123")
    assert b"Welcome, New User" in response.data


def test_user_can_log_in_with_email_address(client, app):
    register(client)
    login(client, "admin", "Admin-Pass-1234!")
    client.post("/admin/approvals/2/approve", follow_redirects=True)
    client.post("/logout", follow_redirects=True)

    response = login(client, "newuser@example.com", "Strong-Pass-123")

    assert b"Welcome, New User" in response.data


def test_fifth_failed_password_attempt_locks_account(client, app):
    register(client)
    login(client, "admin", "Admin-Pass-1234!")
    client.post("/admin/approvals/2/approve", follow_redirects=True)
    client.post("/logout", follow_redirects=True)

    for _ in range(4):
        response = login(client, "newuser", "wrong-password")
        assert b"attempt(s) remain" in response.data

    response = login(client, "newuser", "wrong-password")
    assert b"locked for 15 minutes" in response.data
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "newuser"))
        assert user.failed_attempts == 5
        assert user.locked_until is not None

    response = login(client, "newuser", "Strong-Pass-123")
    assert b"temporarily locked" in response.data


def test_disabled_user_cannot_log_in(client, app):
    register(client)
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "newuser"))
        user.status = "disabled"
        db.session.commit()

    response = login(client, "newuser", "Strong-Pass-123")
    assert b"This account is deactivated" in response.data


def test_registration_displays_field_validation_and_duplicate_errors(client, app):
    response = client.post(
        "/register",
        data={
            "full_name": "",
            "username": "bad username",
            "email": "not-an-email",
            "department_id": "1",
            "role": "data_entry",
            "password": "weak",
            "confirm_password": "different",
        },
    )
    assert response.status_code == 200
    assert b"Enter your full name." in response.data
    assert b"Use only letters, numbers, periods, underscores, and hyphens." in response.data
    assert b"Enter a valid email address." in response.data
    assert b"Use at least three of:" in response.data
    assert b"The passwords do not match." in response.data

    register(client, username="duplicate_user", email="first@example.com")
    response = register(client, username="duplicate_user", email="second@example.com")
    assert b"That employee ID is already in use." in response.data


def test_registration_role_request_and_admin_rejected(client, app):
    response = register(client, username="manager_request", email="manager@example.com", role="manager")
    assert b"Your account is waiting for admin approval." in response.data
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "manager_request"))
        assert user is not None
        assert user.role == "manager"
        assert user.status == "pending"

    response = client.post(
        "/register",
        data={
            "full_name": "Sneaky Admin",
            "username": "sneaky_admin",
            "email": "sneaky@example.com",
            "department_id": "1",
            "role": "admin",
            "password": "Strong-Pass-123",
            "confirm_password": "Strong-Pass-123",
        },
        follow_redirects=True,
    )
    assert b"Your account is waiting for admin approval." not in response.data
    with app.app_context():
        assert db.session.scalar(db.select(User).where(User.username == "sneaky_admin")) is None


def test_development_debug_is_explicit_and_production_stays_disabled():
    assert DevelopmentConfig.DEBUG is True
    assert DevelopmentConfig.ALLOW_DEBUG is True
    assert ProductionConfig.DEBUG is False
    assert ProductionConfig.ALLOW_DEBUG is False


def test_profile_requires_login_and_shows_own_data_with_activity(client, app):
    response = client.get("/profile")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    login(client, "admin", "Admin-Pass-1234!")
    response = client.get("/profile")
    assert response.status_code == 200
    for marker in (
        b"Test Administrator",
        b"admin@example.test",
        b"Administrator",
        b"Change password",
        b"Recent activity",
        b"auth.login",
    ):
        assert marker in response.data
