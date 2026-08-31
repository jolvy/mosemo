from functools import lru_cache

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


class Config(BaseSettings):
    env: str = Field(validation_alias="MOSEMO_ENV")
    database: DatabaseConfig = Field(validation_alias="DB")

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_nested_delimiter="_",
        populate_by_name=True,
    )


@lru_cache
def get_config() -> Config:
    return Config()
