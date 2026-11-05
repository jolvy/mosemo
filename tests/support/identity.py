"""Identity test helpers: cleanup, sign-up dispatcher, and token verification."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import Delete, delete

from mosemo.identity.application.tokens import TokenService
from mosemo.identity.bootstrap import IdentityConfig
from mosemo.identity.infrastructure.tables import accounts
from mosemo.identity.presentation.auth.router import (
    create_current_account_provider,
)
from mosemo.labels import bootstrap as labels_bootstrap
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.presentation.current_account import CurrentAccount


def delete_accounts(*account_ids: UUID) -> Delete:
    """Delete the accounts; their devices, codes, and labels cascade."""
    return delete(accounts).where(accounts.c.id.in_(account_ids))


def sign_up_dispatcher() -> EventDispatcher:
    """A dispatcher with the labels handlers that the API registers."""
    dispatcher = EventDispatcher()
    labels_bootstrap.register_handlers(dispatcher)
    return dispatcher


def token_service(config: IdentityConfig) -> TokenService:
    """The token service that an Identity built from this config uses."""
    return TokenService(
        secret_key=config.jwt_secret_key,
        issuer=config.jwt_issuer,
        audience=config.jwt_audience,
        access_token_ttl_seconds=config.access_token_ttl_seconds,
    )


def current_account_provider(
    config: IdentityConfig,
) -> Callable[..., Awaitable[CurrentAccount]]:
    """The token-verifying current account provider that the API overrides with."""
    return create_current_account_provider(token_service(config))
