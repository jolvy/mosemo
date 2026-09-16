from typing import Annotated, cast

import httpx2
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.accounts.service import AccountService
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.service import ActivityService
from mosemo.auth.context import AuthenticatedAccount
from mosemo.auth.kakao_client import KakaoClient
from mosemo.auth.oauth_client import OAuthClient
from mosemo.auth.repository import NativeAuthCodeRepository
from mosemo.auth.service import AuthService, OAuthClientResolver
from mosemo.auth.tokens import InvalidAccessTokenError, TokenService
from mosemo.config import Config, get_config
from mosemo.database import get_session
from mosemo.devices.repository import DeviceRepository
from mosemo.devices.service import DeviceService
from mosemo.exceptions import ApiException, ErrorCode


def get_http_client(request: Request) -> httpx2.AsyncClient:
    return cast(
        httpx2.AsyncClient,
        request.state.http_client,
    )


HttpClientDep = Annotated[
    httpx2.AsyncClient,
    Depends(get_http_client),
]
ConfigDep = Annotated[Config, Depends(get_config)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_account_repository(session: SessionDep) -> AccountRepository:
    return AccountRepository(session)


AccountRepositoryDep = Annotated[
    AccountRepository,
    Depends(get_account_repository),
]


def get_account_service(
    account_repository: AccountRepositoryDep,
) -> AccountService:
    return AccountService(account_repository=account_repository)


AccountServiceDep = Annotated[
    AccountService,
    Depends(get_account_service),
]


def get_activity_repository(session: SessionDep) -> ActivityRepository:
    return ActivityRepository(session)


ActivityRepositoryDep = Annotated[
    ActivityRepository,
    Depends(get_activity_repository),
]


def get_device_repository(session: SessionDep) -> DeviceRepository:
    return DeviceRepository(session)


DeviceRepositoryDep = Annotated[
    DeviceRepository,
    Depends(get_device_repository),
]


def get_device_service(
    session: SessionDep,
    repository: DeviceRepositoryDep,
) -> DeviceService:
    return DeviceService(session=session, repository=repository)


DeviceServiceDep = Annotated[
    DeviceService,
    Depends(get_device_service),
]


def get_activity_service(
    session: SessionDep,
    repository: ActivityRepositoryDep,
    device_repository: DeviceRepositoryDep,
) -> ActivityService:
    return ActivityService(
        session=session,
        repository=repository,
        device_repository=device_repository,
    )


ActivityServiceDep = Annotated[
    ActivityService,
    Depends(get_activity_service),
]


def get_native_auth_code_repository(
    session: SessionDep,
) -> NativeAuthCodeRepository:
    return NativeAuthCodeRepository(session)


NativeAuthCodeRepositoryDep = Annotated[
    NativeAuthCodeRepository,
    Depends(get_native_auth_code_repository),
]


def get_token_service(config: ConfigDep) -> TokenService:
    return TokenService(config.auth)


TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]


def get_kakao_client(
    config: ConfigDep,
    http_client: HttpClientDep,
) -> KakaoClient:
    return KakaoClient(
        http_client=http_client,
        config=config.kakao,
    )


KakaoClientDep = Annotated[
    KakaoClient,
    Depends(get_kakao_client),
]


def get_oauth_client(
    provider: AccountProvider,
    config: Config,
    http_client: httpx2.AsyncClient,
) -> OAuthClient:
    assert isinstance(provider, AccountProvider)
    if provider is AccountProvider.KAKAO:
        return get_kakao_client(config, http_client)


def get_oauth_client_resolver(
    config: ConfigDep,
    http_client: HttpClientDep,
) -> OAuthClientResolver:
    def resolve(provider: AccountProvider) -> OAuthClient:
        return get_oauth_client(provider, config, http_client)

    return resolve


OAuthClientResolverDep = Annotated[
    OAuthClientResolver,
    Depends(get_oauth_client_resolver),
]


def get_auth_service(
    session: SessionDep,
    account_repository: AccountRepositoryDep,
    native_auth_code_repository: NativeAuthCodeRepositoryDep,
    get_oauth_client: OAuthClientResolverDep,
    token_service: TokenServiceDep,
    config: ConfigDep,
) -> AuthService:
    return AuthService(
        get_oauth_client=get_oauth_client,
        session=session,
        account_repository=account_repository,
        native_auth_code_repository=native_auth_code_repository,
        token_service=token_service,
        config=config.auth,
    )


AuthServiceDep = Annotated[
    AuthService,
    Depends(get_auth_service),
]


bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Mosemo가 발급한 Bearer 액세스 토큰입니다.",
)
BearerCredentialsDep = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(bearer_scheme),
]


async def get_authenticated_account(
    credentials: BearerCredentialsDep,
    token_service: TokenServiceDep,
) -> AuthenticatedAccount:
    if credentials is None:
        raise ApiException(ErrorCode.AUTH_INVALID_ACCESS_TOKEN)

    try:
        account_id = token_service.decode_access_token(credentials.credentials)
    except InvalidAccessTokenError as exc:
        raise ApiException(ErrorCode.AUTH_INVALID_ACCESS_TOKEN) from exc

    return AuthenticatedAccount(account_id=account_id)


AuthenticatedAccountDep = Annotated[
    AuthenticatedAccount,
    Depends(get_authenticated_account),
]
