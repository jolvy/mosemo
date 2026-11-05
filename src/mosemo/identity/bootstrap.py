"""Identity assembly for the assembly roots: mapping, object graph, and routers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Annotated

import httpx2
from fastapi import APIRouter, Depends

from mosemo.identity.application.accounts import AccountDirectory, GetAccount
from mosemo.identity.application.devices import DeviceDirectory, RegisterDevice
from mosemo.identity.application.ports import OAuthClient
from mosemo.identity.application.sign_in import (
    ExchangeAuthorizationCode,
    LogIn,
    OAuthClientResolver,
)
from mosemo.identity.application.tokens import TokenService
from mosemo.identity.domain.accounts import AccountProvider
from mosemo.identity.infrastructure.accounts import AccountRepository
from mosemo.identity.infrastructure.authorization_codes import (
    NativeAuthCodeRepository,
)
from mosemo.identity.infrastructure.devices import DeviceRepository
from mosemo.identity.infrastructure.kakao_client import KakaoClient
from mosemo.identity.infrastructure.queries import AccountQuery, DeviceQuery
from mosemo.identity.infrastructure.tables import map_tables
from mosemo.identity.presentation.accounts.router import (
    create_router as create_accounts_router,
)
from mosemo.identity.presentation.auth.router import (
    create_current_account_provider,
    create_oauth_router,
    create_token_router,
)
from mosemo.identity.presentation.devices.router import (
    create_router as create_devices_router,
)
from mosemo.identity.presentation.error_docs import ERROR_DOCS
from mosemo.platform.providers import get_http_client
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.presentation.current_account import CurrentAccount

__all__ = [
    "ERROR_DOCS",
    "Identity",
    "IdentityConfig",
    "create_account_directory",
    "create_device_directory",
    "create_kakao_client",
    "get_oauth_client",
    "kakao_client_provider",
    "map_tables",
    "oauth_client_resolver_provider",
]

HttpClientDep = Annotated[httpx2.AsyncClient, Depends(get_http_client)]


@dataclass(frozen=True, kw_only=True)
class IdentityConfig:
    """Identity settings that the assembly root reads and validates."""

    kakao_rest_api_key: str
    kakao_client_secret: str = field(repr=False)
    kakao_redirect_uri: str
    jwt_secret_key: str = field(repr=False)
    jwt_issuer: str
    jwt_audience: str
    access_token_ttl_seconds: int
    authorization_code_ttl_seconds: int
    macos_callback_uri: str
    secure_cookies: bool


def create_kakao_client(
    config: IdentityConfig, *, http_client: httpx2.AsyncClient
) -> KakaoClient:
    return KakaoClient(
        http_client=http_client,
        rest_api_key=config.kakao_rest_api_key,
        client_secret=config.kakao_client_secret,
        redirect_uri=config.kakao_redirect_uri,
    )


def kakao_client_provider(
    config: IdentityConfig,
) -> Callable[[httpx2.AsyncClient], OAuthClient]:
    """A per-request dependency that builds the Kakao client for login."""

    def kakao_client(http_client: HttpClientDep) -> OAuthClient:
        return create_kakao_client(config, http_client=http_client)

    return kakao_client


def get_oauth_client(
    provider: AccountProvider,
    config: IdentityConfig,
    *,
    http_client: httpx2.AsyncClient,
) -> OAuthClient:
    assert isinstance(provider, AccountProvider)
    if provider is AccountProvider.KAKAO:
        return create_kakao_client(config, http_client=http_client)


def oauth_client_resolver_provider(
    config: IdentityConfig,
) -> Callable[[httpx2.AsyncClient], OAuthClientResolver]:
    """A per-request dependency that resolves OAuth clients for the callback."""

    def oauth_client_resolver(http_client: HttpClientDep) -> OAuthClientResolver:
        def resolve(provider: AccountProvider) -> OAuthClient:
            return get_oauth_client(provider, config, http_client=http_client)

        return resolve

    return oauth_client_resolver


def create_account_directory() -> AccountDirectory:
    return AccountDirectory(accounts=AccountQuery())


def create_device_directory() -> DeviceDirectory:
    return DeviceDirectory(devices=DeviceQuery())


class Identity:
    """Stateless identity objects built once when an assembly root starts."""

    def __init__(
        self,
        *,
        dispatcher: EventDispatcher,
        clock: Clock,
        config: IdentityConfig,
    ) -> None:
        self.config = config
        self.token_service = TokenService(
            secret_key=config.jwt_secret_key,
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
            access_token_ttl_seconds=config.access_token_ttl_seconds,
        )
        self.get_login_client = kakao_client_provider(config)
        self.get_oauth_clients = oauth_client_resolver_provider(config)
        accounts = AccountRepository()
        auth_codes = NativeAuthCodeRepository()
        self.account_directory = create_account_directory()
        self.device_directory = create_device_directory()
        self.get_account = GetAccount(accounts=accounts)
        self.register_device = RegisterDevice(
            devices=DeviceRepository(), dispatcher=dispatcher
        )
        self.log_in = LogIn(
            accounts=accounts,
            auth_codes=auth_codes,
            dispatcher=dispatcher,
            clock=clock,
            authorization_code_ttl=timedelta(
                seconds=config.authorization_code_ttl_seconds
            ),
        )
        self.exchange_authorization_code = ExchangeAuthorizationCode(
            auth_codes=auth_codes, dispatcher=dispatcher, clock=clock
        )

    def auth_router(self) -> APIRouter:
        router = APIRouter()
        router.include_router(
            create_oauth_router(
                log_in=self.log_in,
                get_login_client=self.get_login_client,
                get_oauth_clients=self.get_oauth_clients,
                macos_callback_uri=self.config.macos_callback_uri,
                secure_cookies=self.config.secure_cookies,
            )
        )
        router.include_router(
            create_token_router(
                exchange_authorization_code=self.exchange_authorization_code,
                token_service=self.token_service,
                access_token_ttl_seconds=self.config.access_token_ttl_seconds,
            )
        )
        return router

    def current_account_provider(self) -> Callable[..., Awaitable[CurrentAccount]]:
        return create_current_account_provider(self.token_service)

    def accounts_router(self) -> APIRouter:
        return create_accounts_router(get_account=self.get_account)

    def devices_router(self) -> APIRouter:
        return create_devices_router(register_device=self.register_device)
