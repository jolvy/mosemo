"""Settings that the API process reads and validates at startup."""

from functools import cache
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mosemo.identity.bootstrap import IdentityConfig
from mosemo.platform.messaging.config import BatchDeliveryConfig

_MIN_JWT_SECRET_KEY_BYTES = 32


class KakaoSettings(BaseModel):
    rest_api_key: str = Field(validation_alias="REST_API_KEY")
    client_secret: SecretStr = Field(validation_alias="CLIENT_SECRET")
    redirect_uri: str = Field(validation_alias="REDIRECT_URI")


class AuthSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    jwt_secret_key: SecretStr = Field(validation_alias="JWT_SECRET_KEY")
    jwt_issuer: str = Field(default="mosemo", validation_alias="JWT_ISSUER")
    jwt_audience: str = Field(
        default="mosemo-api",
        validation_alias="JWT_AUDIENCE",
    )
    access_token_ttl_seconds: int = Field(
        default=86_400,
        gt=0,
        validation_alias="ACCESS_TOKEN_TTL_SECONDS",
    )
    authorization_code_ttl_seconds: int = Field(
        default=60,
        gt=0,
        validation_alias="AUTHORIZATION_CODE_TTL_SECONDS",
    )
    macos_callback_uri: str = Field(validation_alias="MACOS_CALLBACK_URI")

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode()) < _MIN_JWT_SECRET_KEY_BYTES:
            raise ValueError("JWT secret key must be at least 32 bytes")
        return value

    @field_validator("macos_callback_uri")
    @classmethod
    def validate_macos_callback_uri(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            not parsed.scheme
            or parsed.scheme in {"http", "https"}
            or "." not in parsed.scheme
            or parsed.netloc
            or not parsed.path.startswith("/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "macOS callback URI must use a reverse-domain custom scheme "
                "without authority, query, or fragment"
            )
        return value


class ApiConfig(BaseSettings):
    app_env: str = Field(validation_alias="MOSEMO_ENV")
    kakao: KakaoSettings = Field(validation_alias="KAKAO")
    auth: AuthSettings = Field(validation_alias="AUTH")
    batch_delivery: BatchDeliveryConfig = Field(
        default_factory=BatchDeliveryConfig, validation_alias="BATCH"
    )

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_nested_delimiter="_",
        env_nested_max_split=1,
        populate_by_name=True,
    )

    def identity(self) -> IdentityConfig:
        return IdentityConfig(
            kakao_rest_api_key=self.kakao.rest_api_key,
            kakao_client_secret=self.kakao.client_secret.get_secret_value(),
            kakao_redirect_uri=self.kakao.redirect_uri,
            jwt_secret_key=self.auth.jwt_secret_key.get_secret_value(),
            jwt_issuer=self.auth.jwt_issuer,
            jwt_audience=self.auth.jwt_audience,
            access_token_ttl_seconds=self.auth.access_token_ttl_seconds,
            authorization_code_ttl_seconds=self.auth.authorization_code_ttl_seconds,
            macos_callback_uri=self.auth.macos_callback_uri,
            secure_cookies=self.app_env == "prod",
        )


@cache
def get_api_config() -> ApiConfig:
    return ApiConfig()
