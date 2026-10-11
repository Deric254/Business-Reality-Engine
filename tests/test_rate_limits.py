from tests.conftest import register


def test_login_throttled_after_10_attempts(client, tenant_a):
    email, _, _ = register(client, tenant_a)
    from app.core.rate_limit import auth_limiter
    auth_limiter._state.clear()  # register itself counts toward the throttle
    # a different email each time: this test is about the per-IP throttle, not the per-account lockout
    codes = [client.post("/api/auth/login", json={"email": f"nobody{i}@example.com", "password": "wrong-password"}).status_code for i in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]


def test_general_limit_exact(client):
    from app.core.rate_limit import general_limiter
    if not hasattr(general_limiter, "_state"):
        return  # Redis-backed limiter; covered by the multi-process test
    codes = [client.get("/api/health/").status_code for _ in range(301)]
    assert codes.count(200) == 300 and codes[-1] == 429
