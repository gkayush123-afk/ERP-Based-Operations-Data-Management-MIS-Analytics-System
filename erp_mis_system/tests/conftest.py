from datetime import timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models import Department, User


@pytest.fixture()
def app():
    class TestConfig:
        SECRET_KEY = "test-secret-key"
        SQLALCHEMY_DATABASE_URI = "sqlite://"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = False
        # Keep the limiter enabled but effectively unlimited so the shared
        # in-memory counters never trip during the suite; rate-limit tests
        # override these per test.
        RATELIMIT_LOGIN = "1000 per minute"
        RATELIMIT_REGISTER = "1000 per minute"
        PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
        SESSION_COOKIE_HTTPONLY = True
        SESSION_COOKIE_SAMESITE = "Lax"
        REMEMBER_COOKIE_HTTPONLY = True
        REMEMBER_COOKIE_SAMESITE = "Lax"

    app = create_app(TestConfig)
    app.config.update(TESTING=True)
    with app.app_context():
        db.create_all()
        department = Department(name="General")
        db.session.add(department)
        db.session.flush()
        admin = User(
            full_name="Test Administrator",
            username="admin",
            email="admin@example.test",
            department_id=department.id,
            role="admin",
            status="active",
        )
        admin.set_password("Admin-Pass-1234!")
        db.session.add(admin)
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()
