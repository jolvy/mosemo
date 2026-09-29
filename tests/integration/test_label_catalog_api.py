from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import create_autospec
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mosemo.accounts.models import AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.activity_labels.catalog.models import Label
from mosemo.activity_labels.catalog.repository import LabelCatalogRepository
from mosemo.api import v1_api_router
from mosemo.auth.kakao_client import KakaoClient
from mosemo.auth.pkce import create_code_challenge
from mosemo.auth.tokens import TokenService
from mosemo.config import Config, get_config
from mosemo.database import get_session
from mosemo.dependencies import get_kakao_client, get_oauth_client_resolver
from mosemo.exception_handlers import register_exception_handlers


@pytest_asyncio.fixture
async def label_catalog_client(
    integration_database_url: str,
    config: Config,
) -> AsyncIterator[tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(integration_database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session_factory = async_sessionmaker(
                connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )

            app = FastAPI()
            register_exception_handlers(app)
            app.include_router(v1_api_router)
            app.dependency_overrides[get_config] = lambda: config

            async def override_session() -> AsyncIterator[AsyncSession]:
                async with session_factory() as session:
                    yield session

            app.dependency_overrides[get_session] = override_session
            oauth_client = create_autospec(KakaoClient, instance=True)
            oauth_client.get_user_id.return_value = f"catalog-login-{uuid4()}"
            oauth_client.create_authorization_url.return_value = (
                "https://kauth.kakao.com/oauth/authorize"
            )
            app.dependency_overrides[get_kakao_client] = lambda: oauth_client
            app.dependency_overrides[get_oauth_client_resolver] = lambda: (
                lambda provider: oauth_client
            )
            try:
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    yield client, session_factory
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_http_login_prepares_defaults_once_and_preserves_catalog(
    label_catalog_client: tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = label_catalog_client
    verifier = "A" * 43
    catalogs = []
    for _ in range(2):
        login = await client.get(
            "/api/v1/auth/kakao/login",
            params={
                "code_challenge": create_code_challenge(verifier),
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        assert login.status_code == 302
        callback = await client.get(
            "/api/v1/auth/kakao/callback",
            params={
                "code": "provider-code",
                "state": client.cookies.get("kakao_oauth_state"),
            },
            follow_redirects=False,
        )
        assert callback.status_code == 302
        code = parse_qs(urlsplit(callback.headers["location"]).query)["code"][0]
        token = await client.post(
            "/api/v1/auth/token",
            json={
                "grantType": "authorization_code",
                "code": code,
                "codeVerifier": verifier,
            },
        )
        assert token.status_code == 200
        catalog = await client.get(
            "/api/v1/labels",
            headers={"Authorization": f"Bearer {token.json()['accessToken']}"},
        )
        assert catalog.status_code == 200
        catalogs.append(catalog.json())

    assert [label["displayName"] for label in catalogs[0]] == [
        "소통",
        "쇼핑",
        "여가",
        "코딩",
        "학습",
    ]
    assert all(label["archivedAt"] is None for label in catalogs[0])
    assert catalogs[1] == catalogs[0]


@pytest.mark.asyncio
async def test_label_catalog_returns_owned_active_and_archived_labels(
    label_catalog_client: tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession]],
    config: Config,
) -> None:
    client, session_factory = label_catalog_client
    created_at = datetime(2026, 9, 25, 1, 2, 3, tzinfo=UTC)
    updated_at = datetime(2026, 9, 26, 4, 5, 6, tzinfo=UTC)
    archived_at = datetime(2026, 9, 27, 7, 8, 9, tzinfo=UTC)
    async with session_factory() as session:
        owner = AccountRepository(session).save(
            provider=AccountProvider.KAKAO,
            provider_subject=f"label-catalog-{uuid4()}",
        )
        other = AccountRepository(session).save(
            provider=AccountProvider.KAKAO,
            provider_subject=f"label-catalog-{uuid4()}",
        )
        await session.flush()
        defaults = LabelCatalogRepository(session).create_defaults(
            account_id=owner.account_id
        )
        defaults[0].created_at = created_at
        defaults[0].updated_at = updated_at
        defaults[0].archived_at = archived_at
        custom_active = Label(account_id=owner.account_id, display_name="독서")
        custom_archived = Label(
            account_id=owner.account_id,
            display_name="산책",
            created_at=created_at,
            updated_at=updated_at,
            archived_at=archived_at,
        )
        session.add_all(
            [
                custom_active,
                custom_archived,
                Label(account_id=other.account_id, display_name="다른 계정의 라벨"),
            ]
        )
        await session.flush()
        owner_id = owner.account_id
        expected_ids = {
            label.display_name: str(label.label_id)
            for label in [*defaults, custom_active, custom_archived]
        }
        await session.commit()

    token = TokenService(config.auth).issue_access_token(owner_id)
    response = await client.get(
        "/api/v1/labels",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    labels = response.json()
    assert isinstance(labels, list)
    assert len(labels) == 7
    assert [label["displayName"] for label in labels] == sorted(expected_ids)
    by_name = {label["displayName"]: label for label in labels}
    assert set(by_name) == set(expected_ids)
    for name, label in by_name.items():
        assert set(label) == {
            "labelId",
            "displayName",
            "createdAt",
            "updatedAt",
            "archivedAt",
        }
        assert label["labelId"] == expected_ids[name]
    assert by_name["코딩"] == {
        "labelId": expected_ids["코딩"],
        "displayName": "코딩",
        "createdAt": "2026-09-25T01:02:03Z",
        "updatedAt": "2026-09-26T04:05:06Z",
        "archivedAt": "2026-09-27T07:08:09Z",
    }
    assert by_name["산책"]["archivedAt"] == "2026-09-27T07:08:09Z"
    assert by_name["독서"]["archivedAt"] is None
    assert by_name["학습"]["archivedAt"] is None


@pytest.mark.asyncio
async def test_label_catalog_returns_empty_array_for_account_without_labels(
    label_catalog_client: tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession]],
    config: Config,
) -> None:
    client, session_factory = label_catalog_client
    async with session_factory() as session:
        account = AccountRepository(session).save(
            provider=AccountProvider.KAKAO,
            provider_subject=f"label-catalog-empty-{uuid4()}",
        )
        await session.flush()
        account_id = account.account_id
        await session.commit()

    token = TokenService(config.auth).issue_access_token(account_id)
    response = await client.get(
        "/api/v1/labels",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_label_catalog_rejects_missing_and_invalid_tokens(
    label_catalog_client: tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = label_catalog_client
    responses = [
        await client.get("/api/v1/labels"),
        await client.get(
            "/api/v1/labels",
            headers={"Authorization": "Bearer invalid-token"},
        ),
    ]

    for response in responses:
        assert response.status_code == 401
        assert response.json() == {
            "error": {
                "status": "AUTH_INVALID_ACCESS_TOKEN",
                "code": 401,
                "message": "Invalid or expired access token",
                "details": [],
            }
        }
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_label_catalog_rejects_token_for_deleted_account(
    label_catalog_client: tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession]],
    config: Config,
) -> None:
    client, session_factory = label_catalog_client
    async with session_factory() as session:
        account = AccountRepository(session).save(
            provider=AccountProvider.KAKAO,
            provider_subject=f"label-catalog-deleted-{uuid4()}",
        )
        await session.flush()
        token = TokenService(config.auth).issue_access_token(account.account_id)
        await session.delete(account)
        await session.commit()

    response = await client.get(
        "/api/v1/labels",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"] == {
        "status": "AUTH_INVALID_ACCESS_TOKEN",
        "code": 401,
        "message": "Invalid or expired access token",
        "details": [],
    }
    assert response.headers["www-authenticate"] == "Bearer"
