from sqlalchemy.exc import OperationalError


def database_unavailable(*_args, **_kwargs):
    raise OperationalError("SELECT ...", {}, Exception("database unavailable"))


def test_register_shows_database_unavailable_message(client, monkeypatch):
    monkeypatch.setattr("app.auth.routes.db.session.scalars", database_unavailable)

    response = client.get("/register")

    assert response.status_code == 503
    assert b"Registration is temporarily unavailable" in response.data


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
