from app.extensions import limiter


def _enable_limiter(app, login="2 per minute", register="2 per minute"):
    app.config["RATELIMIT_LOGIN"] = login
    app.config["RATELIMIT_REGISTER"] = register
    limiter.reset()


def _login_post(client, username="admin", password="Admin-Pass-1234!"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
    )


def test_login_rate_limit_blocks_burst_and_recovers(client, app):
    _enable_limiter(app)
    assert _login_post(client).status_code == 302
    assert _login_post(client).status_code == 302
    limited = _login_post(client)
    assert limited.status_code == 429
    assert b"Too many requests" in limited.data

    limiter.reset()
    assert _login_post(client).status_code == 302


def test_register_rate_limit_blocks_burst(client, app):
    _enable_limiter(app)

    def attempt(index):
        return client.post(
            "/register",
            data={
                "full_name": "Rate User",
                "username": f"rateuser{index}",
                "email": f"rateuser{index}@example.com",
                "department_id": "1",
                "role": "data_entry",
                "password": "Strong-Pass-123",
                "confirm_password": "Strong-Pass-123",
            },
        )

    assert attempt(1).status_code == 302
    assert attempt(2).status_code == 302
    limited = attempt(3)
    assert limited.status_code == 429
    assert b"Too many requests" in limited.data


def test_rate_limit_counts_post_only_and_ignores_get(client, app):
    _enable_limiter(app, login="1 per minute")
    for _ in range(5):
        assert client.get("/login").status_code == 200
    assert _login_post(client).status_code == 302
    assert _login_post(client).status_code == 429
