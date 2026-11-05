import pytest

from mosemo.entrypoints.api.config import ApiConfig
from mosemo.identity import bootstrap as identity_bootstrap
from mosemo.identity.bootstrap import IdentityConfig
from mosemo.labels import bootstrap as labels_bootstrap
from mosemo.shared.application.events import EventDispatcher
from tests.support.identity import sign_up_dispatcher

# Tests use the entities without an assembly root, so map them as the roots do.
identity_bootstrap.map_tables()
labels_bootstrap.map_tables()


@pytest.fixture
def api_config() -> ApiConfig:
    return ApiConfig(
        app_env="test",
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
def identity_config(api_config: ApiConfig) -> IdentityConfig:
    return api_config.identity()


@pytest.fixture
def sign_up_events() -> EventDispatcher:
    """Dispatcher for sign-up use cases called outside the app (default labels)."""
    return sign_up_dispatcher()
