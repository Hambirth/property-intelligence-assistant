import re


def test_valid_request_id_is_preserved(client) -> None:
    response = client.get("/health", headers={"X-Request-ID": "demo-request-123"})

    assert response.headers["X-Request-ID"] == "demo-request-123"


def test_invalid_request_id_is_replaced(client) -> None:
    response = client.get("/health", headers={"X-Request-ID": "bad value\n"})

    assert response.status_code == 200
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        response.headers["X-Request-ID"],
    )


def test_security_headers_are_added(client) -> None:
    response = client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_cors_allows_only_the_configured_frontend(client) -> None:
    allowed = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    denied = client.options(
        "/health",
        headers={
            "Origin": "https://malicious.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert "Access-Control-Allow-Origin" not in denied.headers


def test_unhandled_errors_return_safe_response(app) -> None:
    from fastapi.testclient import TestClient

    @app.get("/_test/unhandled")
    async def raise_unhandled_error() -> None:
        raise RuntimeError("secret-database-password")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/_test/unhandled")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "secret-database-password" not in response.text
