from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    database: str = Field(validation_alias="DATABASE")
    user: str = Field(validation_alias="USER")
    password: SecretStr = Field(validation_alias="PASSWORD")
    host: str = Field(validation_alias="HOST")
    port: int = Field(validation_alias="PORT")

    @property
    def dsn(self) -> str:
        password = self.password.get_secret_value()
        return f"postgresql+asyncpg://{self.user}:{password}@{self.host}:{self.port}/{self.database}"


class KakaoConfig(BaseModel):
    rest_api_key: str = Field(validation_alias="REST_API_KEY")
    client_secret: SecretStr = Field(validation_alias="CLIENT_SECRET")
    redirect_uri: str = Field(validation_alias="REDIRECT_URI")


class AuthConfig(BaseModel):
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
        if len(value.get_secret_value().encode()) < 32:
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


class Config(BaseSettings):
    app_env: str = Field(validation_alias="MOSEMO_ENV")
    database: DatabaseConfig = Field(validation_alias="DB")
    kakao: KakaoConfig = Field(validation_alias="KAKAO")
    auth: AuthConfig = Field(validation_alias="AUTH")

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_nested_delimiter="_",
        env_nested_max_split=1,
        populate_by_name=True,
    )


@lru_cache
def get_config() -> Config:
    return Config()
