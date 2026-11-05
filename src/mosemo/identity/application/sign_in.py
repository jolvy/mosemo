from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.application.ports import OAuthClient
from mosemo.identity.application.tokens import TokenService
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.authorization_codes import (
    NativeAuthCode,
    authorization_code_digest,
)
from mosemo.identity.domain.oauth import OAuthClientError
from mosemo.identity.domain.pkce import create_code_challenge
from mosemo.identity.infrastructure.accounts import AccountRepository
from mosemo.identity.infrastructure.authorization_codes import (
    NativeAuthCodeRepository,
)
from mosemo.platform.database.transactions import unit_of_work
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.application.use_case import UseCase


class OAuthAuthenticationError(Exception):
    pass


class InvalidAuthorizationCodeError(Exception):
    pass


type OAuthClientResolver = Callable[[AccountProvider], OAuthClient]


class LogIn(UseCase):
    """Sign in through an OAuth provider and issue a one-time authorization code.

    A first sign-in creates the account and records `AccountCreated`; a later
    one updates the last authentication time.
    """

    def __init__(
        self,
        *,
        accounts: AccountRepository,
        auth_codes: NativeAuthCodeRepository,
        dispatcher: EventDispatcher,
        clock: Clock,
        authorization_code_ttl: timedelta,
    ) -> None:
        self._accounts = accounts
        self._auth_codes = auth_codes
        self._dispatcher = dispatcher
        self._clock = clock
        self._authorization_code_ttl = authorization_code_ttl

    async def execute(
        self,
        db_session: AsyncSession,
        *,
        oauth_clients: OAuthClientResolver,
        provider: AccountProvider,
        code: str,
        code_challenge: str,
    ) -> str:
        oauth_client = oauth_clients(provider)
        try:
            provider_subject = await oauth_client.get_user_id(code=code)
        except OAuthClientError as exc:
            raise OAuthAuthenticationError from exc

        async with unit_of_work(db_session, self._dispatcher):
            now = self._clock()
            account, created = await self._accounts.get_or_create(
                db_session,
                Account(provider=provider, provider_subject=provider_subject),
            )
            if created:
                account.mark_created()
            else:
                account.authenticate(now=now)
            await self._auth_codes.delete_expired(db_session, now)
            auth_code, authorization_code = NativeAuthCode.issue(
                account_id=account.id,
                code_challenge=code_challenge,
                now=now,
                ttl=self._authorization_code_ttl,
            )
            self._auth_codes.add(db_session, auth_code)
        return authorization_code


class ExchangeAuthorizationCode(UseCase):
    """Consume a one-time authorization code and issue an access token for it."""

    def __init__(
        self,
        *,
        auth_codes: NativeAuthCodeRepository,
        dispatcher: EventDispatcher,
        clock: Clock,
    ) -> None:
        self._auth_codes = auth_codes
        self._dispatcher = dispatcher
        self._clock = clock

    async def execute(
        self,
        db_session: AsyncSession,
        *,
        authorization_code: str,
        code_verifier: str,
        token_service: TokenService,
    ) -> str:
        try:
            code_challenge = create_code_challenge(code_verifier)
        except ValueError as exc:
            raise InvalidAuthorizationCodeError from exc

        async with unit_of_work(db_session, self._dispatcher):
            stored = await self._auth_codes.find_for_update(
                db_session, authorization_code_digest(authorization_code)
            )
            if stored is None:
                raise InvalidAuthorizationCodeError
            now = self._clock()
            # An expired code is deleted and committed before it is rejected.
            expired = stored.is_expired(now=now)
            if not expired:
                if not stored.matches(code_challenge):
                    raise InvalidAuthorizationCodeError
                # Issue before deleting: a failure keeps the code for a retry.
                access_token = token_service.issue_access_token(
                    stored.account_id, now=now
                )
            await self._auth_codes.delete(db_session, stored)
        if expired:
            raise InvalidAuthorizationCodeError
        return access_token
