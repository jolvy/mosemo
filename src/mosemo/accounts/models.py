from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid7

from sqlalchemy import DateTime, Enum, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from mosemo.database import Base
from mosemo.timezones import Timezone, TimezoneStorage


class AccountProvider(StrEnum):
    KAKAO = "KAKAO"


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_subject"),)

    account_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    provider: Mapped[AccountProvider] = mapped_column(
        Enum(
            AccountProvider,
            name="account_provider",
            values_callable=lambda providers: [
                provider.value for provider in providers
            ],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
        )
    )
    provider_subject: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[Timezone] = mapped_column(
        TimezoneStorage(String(255)),
        server_default=Timezone.ASIA_SEOUL.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    last_authenticated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
