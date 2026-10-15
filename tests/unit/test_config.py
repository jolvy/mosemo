import pytest
from pydantic import ValidationError

from mosemo.config import AuthConfig, BatchDeliveryConfig, Config


def test_batch_topic_is_loaded_from_environment(config: Config, monkeypatch) -> None:
    monkeypatch.setenv("BATCH_TOPIC", "test.custom.activities")
    configured = Config(
        app_env=config.app_env,
        database=config.database,
        kakao=config.kakao,
        auth=config.auth,
    )
    assert configured.batch_delivery.topic == "test.custom.activities"


def test_batch_topic_rejects_empty_value() -> None:
    with pytest.raises(ValidationError):
        BatchDeliveryConfig(topic="")


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
