"""Identity assembly for the assembly roots: mapping, object graph, and routers."""

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
    create_router as create_auth_router,
)
from mosemo.identity.presentation.auth.router import get_token_current_account
from mosemo.identity.presentation.devices.router import (
    create_router as create_devices_router,
)
from mosemo.identity.presentation.error_docs import ERROR_DOCS
from mosemo.platform.config import Config, KakaoConfig
from mosemo.platform.providers import get_config, get_http_client
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher

__all__ = [
    "ERROR_DOCS",
    "Identity",
    "create_account_directory",
    "create_device_directory",
    "create_kakao_client",
    "get_kakao_client",
    "get_oauth_client",
    "get_oauth_client_resolver",
    "get_token_current_account",
    "map_tables",
]

ConfigDep = Annotated[Config, Depends(get_config)]
HttpClientDep = Annotated[httpx2.AsyncClient, Depends(get_http_client)]


def create_kakao_client(
    config: KakaoConfig, *, http_client: httpx2.AsyncClient
) -> KakaoClient:
    return KakaoClient(
        http_client=http_client,
        rest_api_key=config.rest_api_key,
        client_secret=config.client_secret.get_secret_value(),
        redirect_uri=config.redirect_uri,
    )


def get_kakao_client(config: ConfigDep, http_client: HttpClientDep) -> OAuthClient:
    return create_kakao_client(config.kakao, http_client=http_client)


def get_oauth_client(
    provider: AccountProvider,
    config: Config,
    *,
    http_client: httpx2.AsyncClient,
) -> OAuthClient:
    assert isinstance(provider, AccountProvider)
    if provider is AccountProvider.KAKAO:
        return create_kakao_client(config.kakao, http_client=http_client)


def get_oauth_client_resolver(
    config: ConfigDep,
    http_client: HttpClientDep,
) -> OAuthClientResolver:
    def resolve(provider: AccountProvider) -> OAuthClient:
        return get_oauth_client(provider, config, http_client=http_client)

    return resolve


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
        config: Config,
    ) -> None:
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
                seconds=config.auth.authorization_code_ttl_seconds
            ),
        )
        self.exchange_authorization_code = ExchangeAuthorizationCode(
            auth_codes=auth_codes, dispatcher=dispatcher, clock=clock
        )

    def auth_router(self) -> APIRouter:
        return create_auth_router(
            log_in=self.log_in,
            exchange_authorization_code=self.exchange_authorization_code,
            get_login_client=get_kakao_client,
            get_oauth_clients=get_oauth_client_resolver,
        )

    def accounts_router(self) -> APIRouter:
        return create_accounts_router(get_account=self.get_account)

    def devices_router(self) -> APIRouter:
        return create_devices_router(register_device=self.register_device)
