import os

import pytest
from pydantic import ValidationError

from mosemo.entrypoints.api.config import ApiConfig, AuthSettings
from mosemo.entrypoints.batch.config import BatchJobsConfig
from mosemo.entrypoints.consumer.config import ConsumerConfig
from mosemo.identity.bootstrap import IdentityConfig
from mosemo.platform.database.config import DatabaseSettings
from mosemo.platform.messaging.config import BatchDeliveryConfig

DATABASE_ENVIRONMENT = {
    "DB_DATABASE": "mosemo_test",
    "DB_USER": "mosemo",
    "DB_PASSWORD": "test-db-password",
    "DB_HOST": "db.internal",
    "DB_PORT": "6543",
}
BATCH_ENVIRONMENT = {
    "BATCH_ENABLED": "false",
    "BATCH_TOPIC": "test.custom.activities",
    "BATCH_BOOTSTRAP_SERVERS": "broker-a:9092,broker-b:9092",
    "BATCH_GROUP_ID": "test.activity-batches",
}
API_ENVIRONMENT = {
    "MOSEMO_ENV": "prod",
    "KAKAO_REST_API_KEY": "test-rest-api-key",
    "KAKAO_CLIENT_SECRET": "test-client-secret",
    "KAKAO_REDIRECT_URI": "http://localhost:8000/api/v1/auth/kakao/callback",
    "AUTH_JWT_SECRET_KEY": "test-jwt-secret-key-that-is-at-least-32-bytes",
    "AUTH_JWT_ISSUER": "test-issuer",
    "AUTH_JWT_AUDIENCE": "test-audience",
    "AUTH_ACCESS_TOKEN_TTL_SECONDS": "3600",
    "AUTH_AUTHORIZATION_CODE_TTL_SECONDS": "30",
    "AUTH_MACOS_CALLBACK_URI": "com.example.mosemo:/auth/callback",
}
BATCH_JOBS_ENVIRONMENT = {"LABEL_MODEL": "openai:fake-model"}
EVERY_PROCESS_ENVIRONMENT = (
    DATABASE_ENVIRONMENT | BATCH_ENVIRONMENT | API_ENVIRONMENT | BATCH_JOBS_ENVIRONMENT
)
SETTINGS_PREFIXES = ("DB_", "BATCH_", "MOSEMO_ENV", "KAKAO_", "AUTH_", "LABEL_MODEL")


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch):
    """Replace every settings variable with exactly the given ones."""

    def use(values: dict[str, str]) -> None:
        for name in list(os.environ):
            if name.startswith(SETTINGS_PREFIXES):
                monkeypatch.delenv(name)
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return use


def test_database_settings_read_db_variables(environment) -> None:
    environment(DATABASE_ENVIRONMENT)

    database = DatabaseSettings().database

    assert database.dsn == (
        "postgresql+asyncpg://mosemo:test-db-password@db.internal:6543/mosemo_test"
    )


def test_api_config_reads_api_variables(environment) -> None:
    environment(API_ENVIRONMENT | BATCH_ENVIRONMENT)

    config = ApiConfig()

    assert config.app_env == "prod"
    assert config.kakao.rest_api_key == "test-rest-api-key"
    assert config.kakao.client_secret.get_secret_value() == "test-client-secret"
    assert config.auth.jwt_issuer == "test-issuer"
    assert config.auth.jwt_audience == "test-audience"
    assert config.auth.access_token_ttl_seconds == 3600
    assert config.auth.authorization_code_ttl_seconds == 30
    assert config.batch_delivery == BatchDeliveryConfig(
        enabled=False,
        topic="test.custom.activities",
        bootstrap_servers="broker-a:9092,broker-b:9092",
        group_id="test.activity-batches",
    )


def test_consumer_and_batch_jobs_configs_read_their_variables(environment) -> None:
    environment(BATCH_ENVIRONMENT | BATCH_JOBS_ENVIRONMENT)

    assert ConsumerConfig().batch_delivery.topic == "test.custom.activities"
    assert BatchJobsConfig().label_model == "openai:fake-model"


@pytest.mark.parametrize(
    ("values", "settings"),
    [
        (DATABASE_ENVIRONMENT | BATCH_ENVIRONMENT, [DatabaseSettings, ConsumerConfig]),
        (
            DATABASE_ENVIRONMENT | BATCH_JOBS_ENVIRONMENT,
            [DatabaseSettings, BatchJobsConfig],
        ),
        (DATABASE_ENVIRONMENT, [DatabaseSettings]),
        (
            EVERY_PROCESS_ENVIRONMENT,
            [DatabaseSettings, ApiConfig, ConsumerConfig, BatchJobsConfig],
        ),
    ],
    ids=["consumer", "batch", "migrations", "every-process-variable"],
)
def test_each_process_loads_with_only_its_variables(
    environment, values, settings
) -> None:
    environment(values)

    for setting in settings:
        setting()


@pytest.mark.parametrize(
    "missing", ["MOSEMO_ENV", "KAKAO_REST_API_KEY", "AUTH_JWT_SECRET_KEY"]
)
def test_api_config_requires_its_variables(environment, missing: str) -> None:
    environment({k: v for k, v in API_ENVIRONMENT.items() if k != missing})

    with pytest.raises(ValidationError):
        ApiConfig()


@pytest.mark.parametrize("label_model", [None, ""])
def test_batch_jobs_config_rejects_missing_or_empty_label_model(
    environment, label_model: str | None
) -> None:
    environment({} if label_model is None else {"LABEL_MODEL": label_model})

    with pytest.raises(ValidationError):
        BatchJobsConfig()


def test_database_settings_require_db_variables(environment) -> None:
    environment(API_ENVIRONMENT)

    with pytest.raises(ValidationError):
        DatabaseSettings()


def test_batch_topic_rejects_empty_value() -> None:
    with pytest.raises(ValidationError):
        BatchDeliveryConfig(topic="")


@pytest.mark.parametrize("field", ["bootstrap_servers", "group_id"])
def test_batch_connection_settings_reject_empty_values(field):
    with pytest.raises(ValidationError):
        BatchDeliveryConfig(**{field: ""})


def test_auth_settings_reject_short_jwt_secret() -> None:
    with pytest.raises(ValidationError):
        AuthSettings(
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
def test_auth_settings_reject_invalid_callback_uri(callback_uri: str) -> None:
    with pytest.raises(ValidationError):
        AuthSettings(
            jwt_secret_key="test-jwt-secret-key-that-is-at-least-32-bytes",
            macos_callback_uri=callback_uri,
        )


@pytest.mark.parametrize(
    ("app_env", "secure_cookies"), [("prod", True), ("test", False), ("local", False)]
)
def test_identity_config_uses_secure_cookies_only_in_prod(
    api_config: ApiConfig, app_env: str, *, secure_cookies: bool
) -> None:
    config = api_config.model_copy(update={"app_env": app_env}).identity()

    assert config.secure_cookies is secure_cookies


def test_identity_config_carries_api_values(api_config: ApiConfig) -> None:
    assert api_config.identity() == IdentityConfig(
        kakao_rest_api_key="test-rest-api-key",
        kakao_client_secret="test-client-secret",
        kakao_redirect_uri="http://localhost:8000/api/v1/auth/kakao/callback",
        jwt_secret_key="test-jwt-secret-key-that-is-at-least-32-bytes",
        jwt_issuer="mosemo",
        jwt_audience="mosemo-api",
        access_token_ttl_seconds=86_400,
        authorization_code_ttl_seconds=60,
        macos_callback_uri="com.example.mosemo:/auth/callback",
        secure_cookies=False,
    )


def test_identity_config_repr_hides_secrets(identity_config: IdentityConfig) -> None:
    assert "test-client-secret" not in repr(identity_config)
    assert "test-jwt-secret-key" not in repr(identity_config)
    assert "test-rest-api-key" in repr(identity_config)
