import hashlib
import secrets
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.activity_labels.catalog.models import default_labels
from mosemo.activity_labels.catalog.repository import LabelCatalogRepository
from mosemo.auth.models import NativeAuthCode
from mosemo.auth.oauth_client import OAuthClient, OAuthClientError
from mosemo.auth.pkce import create_code_challenge
from mosemo.auth.repository import NativeAuthCodeRepository
from mosemo.auth.tokens import TokenService
from mosemo.clock import Clock
from mosemo.config import AuthConfig


class OAuthAuthenticationError(Exception):
    pass


class InvalidAuthorizationCodeError(Exception):
    pass


class AuthorizationCodeExpiredError(Exception):
    pass


type OAuthClientResolver = Callable[[AccountProvider], OAuthClient]


class AccountProvisioner:
    def __init__(
        self,
        *,
        account_repository: AccountRepository,
        label_catalog_repository: LabelCatalogRepository,
    ) -> None:
        self._account_repository = account_repository
        self._label_catalog_repository = label_catalog_repository

    async def provision(
        self,
        *,
        provider: AccountProvider,
        provider_subject: str,
        now: datetime,
    ) -> Account:
        account = await self._account_repository.add_if_absent(
            provider=provider,
            provider_subject=provider_subject,
        )
        if account is not None:
            self._label_catalog_repository.add_all(default_labels(account.account_id))
        else:
            account = await self._account_repository.find(
                provider=provider,
                provider_subject=provider_subject,
            )
            if account is None:
                raise RuntimeError("account conflict could not be resolved")
            account.last_authenticated_at = now
        return account


class NativeAuthCodeService:
    def __init__(
        self,
        *,
        repository: NativeAuthCodeRepository,
        token_service: TokenService,
        config: AuthConfig,
    ) -> None:
        self._repository = repository
        self._token_service = token_service
        self._config = config

    async def issue(
        self,
        *,
        account_id: UUID,
        code_challenge: str,
        now: datetime,
    ) -> str:
        authorization_code = secrets.token_urlsafe(32)
        code_digest = hashlib.sha256(authorization_code.encode()).hexdigest()

        await self._repository.delete_expired(now)
        self._repository.add(
            NativeAuthCode(
                code_digest=code_digest,
                account_id=account_id,
                code_challenge=code_challenge,
                expires_at=now
                + timedelta(seconds=self._config.authorization_code_ttl_seconds),
            )
        )
        return authorization_code

    async def exchange(
        self,
        *,
        authorization_code: str,
        code_challenge: str,
        now: datetime,
    ) -> str:
        code_digest = hashlib.sha256(authorization_code.encode()).hexdigest()
        stored_code = await self._repository.find_for_update(code_digest)
        if stored_code is None:
            raise InvalidAuthorizationCodeError

        if stored_code.expires_at <= now:
            await self._repository.delete(stored_code)
            raise AuthorizationCodeExpiredError

        if not secrets.compare_digest(stored_code.code_challenge, code_challenge):
            raise InvalidAuthorizationCodeError

        access_token = self._token_service.issue_access_token(
            stored_code.account_id, now=now
        )
        await self._repository.delete(stored_code)
        return access_token


class AuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        get_oauth_client: OAuthClientResolver,
        account_provisioner: AccountProvisioner,
        native_auth_codes: NativeAuthCodeService,
        clock: Clock,
    ) -> None:
        self._session = session
        self._get_oauth_client = get_oauth_client
        self._account_provisioner = account_provisioner
        self._native_auth_codes = native_auth_codes
        self._clock = clock

    async def login(
        self,
        *,
        provider: AccountProvider,
        code: str,
        code_challenge: str,
    ) -> str:
        oauth_client = self._get_oauth_client(provider)
        try:
            provider_subject = await oauth_client.get_user_id(code=code)
        except OAuthClientError as exc:
            raise OAuthAuthenticationError from exc

        async with self._session.begin():
            now = self._clock()
            account = await self._account_provisioner.provision(
                provider=provider,
                provider_subject=provider_subject,
                now=now,
            )
            authorization_code = await self._native_auth_codes.issue(
                account_id=account.account_id,
                code_challenge=code_challenge,
                now=now,
            )

        return authorization_code

    async def exchange_authorization_code(
        self,
        *,
        authorization_code: str,
        code_verifier: str,
    ) -> str:
        try:
            code_challenge = create_code_challenge(code_verifier)
        except ValueError as exc:
            raise InvalidAuthorizationCodeError from exc

        async with self._session.begin():
            # 만료 코드 삭제는 커밋한 뒤 거절한다
            with suppress(AuthorizationCodeExpiredError):
                return await self._native_auth_codes.exchange(
                    authorization_code=authorization_code,
                    code_challenge=code_challenge,
                    now=self._clock(),
                )

        raise InvalidAuthorizationCodeError
