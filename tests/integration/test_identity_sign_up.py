"""Identity sign-up events and account checks of other contexts (#149, #150 AC-11, AC-12)."""

from collections.abc import AsyncIterator
from datetime import timedelta
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import httpx2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mosemo.api.exception_handlers import register_exception_handlers
from mosemo.api.routes import v1_api_router
from mosemo.identity.application.sign_in import LogIn
from mosemo.identity.bootstrap import get_token_current_account
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.events import AccountCreated
from mosemo.identity.domain.pkce import create_code_challenge
from mosemo.identity.infrastructure.accounts import AccountRepository
from mosemo.identity.infrastructure.authorization_codes import (
    NativeAuthCodeRepository,
)
from mosemo.identity.infrastructure.kakao_client import KakaoClient
from mosemo.identity.infrastructure.tables import accounts
from mosemo.identity.presentation.auth.router import get_token_service
from mosemo.infra.clock import utc_now
from mosemo.infra.config import Config, get_config
from mosemo.infra.providers import get_session
from mosemo.labels.domain.labels import DEFAULT_LABEL_NAMES, Label
from mosemo.labels.infrastructure.tables import labels as label_table
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.presentation.current_account import get_current_account
from tests.clock_support import FakeClock
from tests.identity_support import delete_accounts

CODE_CHALLENGE = create_code_challenge("A" * 43)


def make_log_in(dispatcher: EventDispatcher) -> LogIn:
    return LogIn(
        accounts=AccountRepository(),
        auth_codes=NativeAuthCodeRepository(),
        dispatcher=dispatcher,
        clock=FakeClock(utc_now()),
        authorization_code_ttl=timedelta(seconds=60),
    )


async def log_in(session: AsyncSession, use_case: LogIn, provider_subject: str) -> str:
    oauth_client = create_autospec(KakaoClient, instance=True)
    oauth_client.get_user_id.return_value = provider_subject
    return await use_case.execute(
        session,
        oauth_clients=lambda _provider: oauth_client,
        provider=AccountProvider.KAKAO,
        code="authorization-code",
        code_challenge=CODE_CHALLENGE,
    )


async def find_account(session: AsyncSession, provider_subject: str) -> Account | None:
    return await session.scalar(
        select(Account).where(accounts.c.provider_subject == provider_subject)
    )


@pytest.mark.asyncio
async def test_sign_up_creates_default_labels_in_the_same_transaction(
    integration_session: AsyncSession,
    sign_up_events: EventDispatcher,
) -> None:
    provider_subject = f"sign-up-{uuid4()}"

    await log_in(integration_session, make_log_in(sign_up_events), provider_subject)

    account = await find_account(integration_session, provider_subject)
    assert account is not None
    labels = (
        await integration_session.scalars(
            select(Label).where(label_table.c.account_id == account.id)
        )
    ).all()
    assert sorted(label.display_name for label in labels) == sorted(DEFAULT_LABEL_NAMES)


@pytest.mark.asyncio
async def test_sign_up_handler_failure_rolls_back_the_account(
    integration_session: AsyncSession,
) -> None:
    provider_subject = f"sign-up-failure-{uuid4()}"
    dispatcher = EventDispatcher()

    async def fail(event: AccountCreated, db_session: AsyncSession) -> None:
        raise RuntimeError("default labels failed")

    dispatcher.subscribe(AccountCreated, fail)

    with pytest.raises(RuntimeError, match="default labels failed"):
        await log_in(integration_session, make_log_in(dispatcher), provider_subject)

    assert await find_account(integration_session, provider_subject) is None


@pytest.mark.asyncio
async def test_labels_handler_failure_rolls_back_the_account(
    integration_session: AsyncSession,
    sign_up_events: EventDispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_subject = f"sign-up-labels-failure-{uuid4()}"

    def duplicate_labels(account_id: UUID) -> list[Label]:
        return [
            Label(account_id=account_id, display_name="코딩"),
            Label(account_id=account_id, display_name="코딩"),
        ]

    monkeypatch.setattr(
        "mosemo.labels.application.labels.default_labels", duplicate_labels
    )

    with pytest.raises(IntegrityError):
        await log_in(integration_session, make_log_in(sign_up_events), provider_subject)

    assert await find_account(integration_session, provider_subject) is None


@pytest.mark.asyncio
async def test_existing_account_login_records_no_account_created(
    integration_session: AsyncSession,
) -> None:
    provider_subject = f"sign-in-again-{uuid4()}"
    created: list[AccountCreated] = []
    dispatcher = EventDispatcher()

    async def record(event: AccountCreated, db_session: AsyncSession) -> None:
        created.append(event)

    dispatcher.subscribe(AccountCreated, record)
    use_case = make_log_in(dispatcher)

    await log_in(integration_session, use_case, provider_subject)
    await log_in(integration_session, use_case, provider_subject)

    account = await find_account(integration_session, provider_subject)
    assert account is not None
    assert created == [AccountCreated(account_id=account.id)]


@pytest_asyncio.fixture
async def api_client(
    integration_database_url: str, config: Config
) -> AsyncIterator[httpx2.AsyncClient]:
    engine = create_async_engine(integration_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app = register_app()
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_session] = override_session
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        await engine.dispose()


def register_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_current_account] = get_token_current_account
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/accounts/me",
        "/api/v1/labels",
        "/api/v1/activities/timeline?date=2026-09-14",
        "/api/v1/activities/label-timeline?date=2026-09-14",
        "/api/v1/focus-sessions?date=2026-09-14",
    ],
)
async def test_valid_token_without_account_is_rejected(
    api_client: httpx2.AsyncClient, config: Config, path: str
) -> None:
    token = get_token_service(config).issue_access_token(uuid4(), now=utc_now())

    response = await api_client.get(path, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["error"]["status"] == "AUTH_INVALID_ACCESS_TOKEN"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_valid_token_with_account_passes_the_account_check(
    api_client: httpx2.AsyncClient,
    integration_database_url: str,
    config: Config,
) -> None:
    engine = create_async_engine(integration_database_url)
    account = Account(provider=AccountProvider.KAKAO, provider_subject=str(uuid4()))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(account)
            await session.commit()
        token = get_token_service(config).issue_access_token(account.id, now=utc_now())

        response = await api_client.get(
            "/api/v1/accounts/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.json()["accountId"] == str(account.id)
    finally:
        async with AsyncSession(engine) as session:
            await session.execute(delete_accounts(account.id))
            await session.commit()
        await engine.dispose()
