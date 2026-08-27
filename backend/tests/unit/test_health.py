from app.db.session import get_db_session


class HealthySession:
    async def execute(self, _statement: object) -> None:
        return None


class UnavailableSession:
    async def execute(self, _statement: object) -> None:
        raise RuntimeError("database URL and password must not reach the response")


async def healthy_session_override():
    yield HealthySession()


async def unavailable_session_override():
    yield UnavailableSession()


def test_health_is_independent_of_database(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_production_disables_docs_redoc_and_openapi(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app import main as main_module
    from app.core.config import Settings

    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://user:password@db:5432/app",
        frontend_url="https://property.example",
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    with TestClient(main_module.create_app()) as production_client:
        assert production_client.get("/docs").status_code == 404
        assert production_client.get("/redoc").status_code == 404
        assert production_client.get("/openapi.json").status_code == 404


def test_ready_succeeds_when_database_responds(app, client) -> None:
    app.dependency_overrides[get_db_session] = healthy_session_override

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}


def test_ready_fails_safely_when_database_is_unavailable(app, client) -> None:
    app.dependency_overrides[get_db_session] = unavailable_session_override

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"] == {"database": "error"}
    assert "password" not in response.text
