import pytest
from pydantic import ValidationError

from mosemo.config import AuthConfig


def test_auth_config_rejects_short_jwt_secret() -> None:
    with pytest.raises(ValidationError):
        AuthConfig(
            jwt_secret_key="short",
            macos_callback_uri="com.example.mosemo:/auth/callback",
        )


@pytest.mark.parametrize(
    "callback_uri",
    [
        "https://example.com/auth/callback",
        "mosemo:/auth/callback",
        "com.example.mosemo://auth/callback",
        "com.example.mosemo:/auth/callback?next=other",
    ],
)
def test_auth_config_rejects_invalid_callback_uri(callback_uri: str) -> None:
    with pytest.raises(ValidationError):
        AuthConfig(
            jwt_secret_key="test-jwt-secret-key-that-is-at-least-32-bytes",
            macos_callback_uri=callback_uri,
        )
