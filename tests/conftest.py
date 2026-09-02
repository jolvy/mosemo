import pytest

from mosemo.config import Config


@pytest.fixture
def config() -> Config:
    return Config(
        app_env="test",
        database={
            "database": "mosemo_test",
            "user": "mosemo",
            "password": "mosemo",
            "host": "localhost",
            "port": 5432,
        },
        kakao={
            "REST_API_KEY": "test-rest-api-key",
            "CLIENT_SECRET": "test-client-secret",
            "REDIRECT_URI": "http://localhost:8000/api/v1/auth/kakao/callback",
        },
    )
