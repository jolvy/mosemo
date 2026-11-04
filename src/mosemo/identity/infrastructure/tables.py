from uuid import uuid7

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapper

from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.authorization_codes import NativeAuthCode
from mosemo.identity.domain.devices import Device
from mosemo.infra.persistence import ORM_METADATA, TimezoneStorage, mapper_registry
from mosemo.shared.domain.timezones import Timezone

accounts = Table(
    "accounts",
    ORM_METADATA,
    Column("id", Uuid, primary_key=True, default=uuid7),
    Column(
        "provider",
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
        ),
        nullable=False,
    ),
    Column("provider_subject", String(255), nullable=False),
    Column(
        "timezone",
        TimezoneStorage(String(255)),
        nullable=False,
        server_default=Timezone.ASIA_SEOUL.value,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "last_authenticated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    UniqueConstraint("provider", "provider_subject"),
)

devices = Table(
    "devices",
    ORM_METADATA,
    Column("id", Uuid, primary_key=True, default=uuid7),
    Column(
        "account_id",
        Uuid,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("idempotency_key", Uuid, nullable=False),
    UniqueConstraint("account_id", "idempotency_key"),
)

native_auth_codes = Table(
    "native_auth_codes",
    ORM_METADATA,
    Column("code_digest", String(64), primary_key=True),
    Column(
        "account_id",
        Uuid,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("code_challenge", String(43), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)


def map_tables() -> None:
    """Map the identity entities to their tables once per process."""
    mapped = {mapper.class_ for mapper in mapper_registry.mappers}
    entities: list[tuple[type, Table]] = [
        (Account, accounts),
        (Device, devices),
        (NativeAuthCode, native_auth_codes),
    ]
    for entity, table in entities:
        if entity not in mapped:
            _map(entity, table)


def _map(entity: type, table: Table) -> Mapper:
    return mapper_registry.map_imperatively(entity, table)
