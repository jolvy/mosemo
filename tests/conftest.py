import pytest

from mosemo.identity import bootstrap as identity_bootstrap
from mosemo.infra.config import Config
from mosemo.shared.application.events import EventDispatcher
from tests.identity_support import sign_up_dispatcher

# Tests use the entities without an assembly root, so map them as the roots do.
identity_bootstrap.map_tables()


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
        auth={
            "JWT_SECRET_KEY": "test-jwt-secret-key-that-is-at-least-32-bytes",
            "MACOS_CALLBACK_URI": "com.example.mosemo:/auth/callback",
        },
    )


@pytest.fixture
def sign_up_events() -> EventDispatcher:
    """Dispatcher for sign-up use cases called outside the app (default labels)."""
    return sign_up_dispatcher()
