from typing import Annotated, cast

import httpx2
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account
from mosemo.accounts.repository import AccountRepository
from mosemo.auth.kakao_client import KakaoClient
from mosemo.auth.repository import NativeAuthCodeRepository
from mosemo.auth.service import AuthService
from mosemo.auth.tokens import InvalidAccessTokenError, TokenService
from mosemo.config import Config, get_config
from mosemo.database import get_session
from mosemo.exceptions import InvalidAccessTokenApiException


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


def get_auth_service(
    session: SessionDep,
    account_repository: AccountRepositoryDep,
    native_auth_code_repository: NativeAuthCodeRepositoryDep,
    kakao_client: KakaoClientDep,
    token_service: TokenServiceDep,
    config: ConfigDep,
) -> AuthService:
    return AuthService(
        kakao_client=kakao_client,
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


bearer_scheme = HTTPBearer(auto_error=False)
BearerCredentialsDep = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(bearer_scheme),
]


async def get_current_account(
    credentials: BearerCredentialsDep,
    token_service: TokenServiceDep,
    account_repository: AccountRepositoryDep,
) -> Account:
    if credentials is None:
        raise InvalidAccessTokenApiException()

    try:
        account_id = token_service.decode_access_token(credentials.credentials)
    except InvalidAccessTokenError as exc:
        raise InvalidAccessTokenApiException() from exc

    account = await account_repository.find_by_id(account_id)
    if account is None:
        raise InvalidAccessTokenApiException()
    return account


CurrentAccountDep = Annotated[
    Account,
    Depends(get_current_account),
]
