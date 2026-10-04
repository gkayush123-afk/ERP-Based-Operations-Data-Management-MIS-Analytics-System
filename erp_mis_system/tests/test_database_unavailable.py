from sqlalchemy.exc import OperationalError, ProgrammingError


def database_unavailable(*_args, **_kwargs):
    raise OperationalError("SELECT ...", {}, Exception("database unavailable"))


def schema_missing(*_args, **_kwargs):
    raise ProgrammingError("SELECT ...", {}, Exception("table does not exist"))


def test_register_shows_database_unavailable_message(client, monkeypatch):
    monkeypatch.setattr("app.auth.routes.db.session.scalars", database_unavailable)

    response = client.get("/register")

    assert response.status_code == 503
    assert b"Registration is temporarily unavailable" in response.data


def test_register_shows_service_unavailable_when_schema_is_missing(client, monkeypatch):
    monkeypatch.setattr("app.auth.routes.db.session.scalars", schema_missing)

    response = client.get("/register")

    assert response.status_code == 503
    assert b"database is not ready" in response.data


def test_login_shows_service_unavailable_when_schema_is_missing(client, monkeypatch):
    monkeypatch.setattr("app.auth.routes.db.session.scalar", schema_missing)

    response = client.post(
        "/login",
        data={"username": "demo_admin", "password": "irrelevant-password"},
    )

    assert response.status_code == 503
    assert b"database is not ready" in response.data


def test_admin_create_user_shows_database_unavailable_message(client, monkeypatch):
    client.post(
        "/login",
        data={"username": "admin", "password": "Admin-Pass-1234!"},
    )
    monkeypatch.setattr(
        "app.users.routes.active_department_choices",
        database_unavailable,
    )

    response = client.get("/admin/users/create")

    assert response.status_code == 503
    assert b"User creation is temporarily unavailable" in response.data
