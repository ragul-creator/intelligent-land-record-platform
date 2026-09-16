from fastapi.testclient import TestClient

from app.main import app


def test_readiness_returns_database_details(monkeypatch) -> None:
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement):
            class Result:
                @staticmethod
                def scalar_one() -> str:
                    return "3.4.0"

            return Result()

    class FakeEngine:
        @staticmethod
        def connect() -> FakeConnection:
            return FakeConnection()

    monkeypatch.setattr("app.main.engine", FakeEngine())
    monkeypatch.setattr("app.main.Redis.from_url", lambda _url: type("RedisClient", (), {"ping": lambda self: True})())
    monkeypatch.setattr("app.main.get_storage_service", lambda: type("Storage", (), {"ensure_private_bucket": lambda self: None})())

    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "postgis_version": "3.4.0"}
