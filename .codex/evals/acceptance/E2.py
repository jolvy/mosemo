"""E2 판정: `PUT /api/v1/labels/{label_id}/archive` HTTP 블랙박스 Integration 테스트.

`run.py`가 평가 작업 공간의 `tests/integration/labels/`에 복사해 실행한다.
base commit에 있던 심볼과 fixture만 쓴다.
"""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mosemo.entrypoints.api.contexts import IDENTITY
from mosemo.entrypoints.api.exception_handlers import register_exception_handlers
from mosemo.entrypoints.api.routes import v1_api_router
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.labels.domain.labels import Label
from mosemo.platform.clock import utc_now
from mosemo.platform.database.engine import get_session
from mosemo.shared.presentation.current_account import get_current_account

OPENAPI = Path(__file__).resolve().parents[3] / "openapi" / "openapi.json"
NOT_AVAILABLE = {
    "error": {
        "status": "LABEL_NOT_AVAILABLE",
        "code": 404,
        "message": "Label not found or inactive",
        "details": [],
    }
}
CREATED_AT = datetime(2026, 9, 25, 1, 2, 3, tzinfo=UTC)
UPDATED_AT = datetime(2026, 9, 26, 4, 5, 6, tzinfo=UTC)
ARCHIVED_AT = datetime(2026, 9, 27, 7, 8, 9, tzinfo=UTC)

type Client = tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession]]


@pytest_asyncio.fixture
async def labels_client(integration_database_url: str) -> AsyncIterator[Client]:
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
            app.dependency_overrides[get_current_account] = (
                IDENTITY.current_account_provider()
            )

            async def override_session() -> AsyncIterator[AsyncSession]:
                async with session_factory() as session:
                    yield session

            app.dependency_overrides[get_session] = override_session
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


async def seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[UUID, dict[str, UUID]]:
    """소유자 계정의 사용 중·보관 라벨과 다른 계정의 라벨을 만든다."""
    async with session_factory() as session:
        owner = Account(
            provider=AccountProvider.KAKAO, provider_subject=f"eval-{uuid4()}"
        )
        other = Account(
            provider=AccountProvider.KAKAO, provider_subject=f"eval-{uuid4()}"
        )
        session.add_all([owner, other])
        await session.flush()
        active = Label(account_id=owner.id, display_name="독서")
        archived = Label(
            account_id=owner.id, display_name="산책", archived_at=ARCHIVED_AT
        )
        archived.created_at = CREATED_AT
        archived.updated_at = UPDATED_AT
        foreign = Label(account_id=other.id, display_name="다른 계정")
        session.add_all([active, archived, foreign])
        await session.flush()
        ids = {"active": active.id, "archived": archived.id, "foreign": foreign.id}
        owner_id = owner.id
        await session.commit()
    return owner_id, ids


def bearer(account_id: UUID) -> dict[str, str]:
    token = IDENTITY.token_service.issue_access_token(account_id, now=utc_now())
    return {"Authorization": f"Bearer {token}"}


def archived_times(response: httpx2.Response) -> dict[str, str | None]:
    assert response.status_code == 200
    return {label["id"]: label["archivedAt"] for label in response.json()}


@pytest.mark.asyncio
async def test_archives_active_label_at_request_time(labels_client: Client) -> None:
    client, session_factory = labels_client
    owner_id, ids = await seed(session_factory)
    headers = bearer(owner_id)

    before = utc_now().replace(microsecond=0)
    response = await client.put(
        f"/api/v1/labels/{ids['active']}/archive", headers=headers
    )
    after = utc_now()

    assert response.status_code == 204
    assert response.content == b""
    archived_at = archived_times(await client.get("/api/v1/labels", headers=headers))
    stamp = archived_at[str(ids["active"])]
    assert stamp is not None
    assert before <= datetime.fromisoformat(stamp) <= after


@pytest.mark.asyncio
async def test_keeps_archived_at_of_archived_label(labels_client: Client) -> None:
    client, session_factory = labels_client
    owner_id, ids = await seed(session_factory)
    headers = bearer(owner_id)

    response = await client.put(
        f"/api/v1/labels/{ids['archived']}/archive", headers=headers
    )

    assert response.status_code == 204
    archived_at = archived_times(await client.get("/api/v1/labels", headers=headers))
    assert archived_at[str(ids["archived"])] == "2026-09-27T07:08:09Z"


@pytest.mark.asyncio
async def test_hides_foreign_and_missing_labels(labels_client: Client) -> None:
    client, session_factory = labels_client
    owner_id, ids = await seed(session_factory)

    for label_id in (ids["foreign"], uuid4()):
        response = await client.put(
            f"/api/v1/labels/{label_id}/archive", headers=bearer(owner_id)
        )

        assert response.status_code == 404
        assert response.json() == NOT_AVAILABLE
    async with session_factory() as session:
        foreign = await session.get(Label, ids["foreign"])
        assert foreign is not None
        assert foreign.archived_at is None


@pytest.mark.asyncio
async def test_rejects_missing_token(labels_client: Client) -> None:
    client, _ = labels_client

    response = await client.put(f"/api/v1/labels/{uuid4()}/archive")

    assert response.status_code == 401
    assert response.json()["error"]["status"] == "AUTH_INVALID_ACCESS_TOKEN"


def test_openapi_snapshot_has_operation() -> None:
    paths = json.loads(OPENAPI.read_text(encoding="utf-8"))["paths"]

    assert "put" in paths["/api/v1/labels/{label_id}/archive"]
