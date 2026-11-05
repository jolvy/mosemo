from functools import cache

from pydantic import BaseModel, ConfigDict, Field, SecretStr
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


class DatabaseSettings(BaseSettings):
    """The DB_* settings that every process reads."""

    database: DatabaseConfig = Field(validation_alias="DB")

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_nested_delimiter="_",
        env_nested_max_split=1,
        populate_by_name=True,
    )


@cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()
