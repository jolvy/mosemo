from typing import Annotated, cast

import httpx2
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.repository import AccountRepository
from mosemo.auth.kakao_client import KakaoClient
from mosemo.auth.service import AuthService
from mosemo.config import Config, get_config
from mosemo.database import get_session


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
    kakao_client: KakaoClientDep,
) -> AuthService:
    return AuthService(
        kakao_client=kakao_client,
        session=session,
        account_repository=account_repository,
    )


AuthServiceDep = Annotated[
    AuthService,
    Depends(get_auth_service),
]
