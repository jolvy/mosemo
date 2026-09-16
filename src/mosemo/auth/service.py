import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.auth.oauth_client import OAuthClient, OAuthClientError
from mosemo.auth.pkce import create_code_challenge
from mosemo.auth.repository import NativeAuthCodeRepository
from mosemo.auth.tokens import TokenService
from mosemo.config import AuthConfig


class OAuthAuthenticationError(Exception):
    pass


class InvalidAuthorizationCodeError(Exception):
    pass


class AuthService:
    def __init__(
        self,
        *,
        oauth_client: OAuthClient,
        provider: AccountProvider,
        session: AsyncSession,
        account_repository: AccountRepository,
        native_auth_code_repository: NativeAuthCodeRepository,
        token_service: TokenService,
        config: AuthConfig,
    ) -> None:
        self._oauth_client = oauth_client
        self._provider = provider
        self._session = session
        self._account_repository = account_repository
        self._native_auth_code_repository = native_auth_code_repository
        self._token_service = token_service
        self._config = config

    async def login(
        self,
        *,
        code: str,
        code_challenge: str,
    ) -> str:
        try:
            provider_subject = await self._oauth_client.get_user_id(code=code)
        except OAuthClientError as exc:
            raise OAuthAuthenticationError from exc

        async with self._session.begin():
            account = await self._account_repository.find(
                provider=self._provider,
                provider_subject=provider_subject,
            )
            if account is None:
                account = self._account_repository.save(
                    provider=self._provider,
                    provider_subject=provider_subject,
                )
                await self._session.flush()
            else:
                account.last_authenticated_at = datetime.now(UTC)

            authorization_code = await self._create_authorization_code(
                account=account,
                code_challenge=code_challenge,
            )

        return authorization_code

    async def _create_authorization_code(
        self,
        *,
        account: Account,
        code_challenge: str,
    ) -> str:
        now = datetime.now(UTC)
        authorization_code = secrets.token_urlsafe(32)
        code_digest = hashlib.sha256(authorization_code.encode()).hexdigest()

        await self._native_auth_code_repository.delete_expired(now)
        self._native_auth_code_repository.save(
            code_digest=code_digest,
            account_id=account.account_id,
            code_challenge=code_challenge,
            expires_at=now
            + timedelta(seconds=self._config.authorization_code_ttl_seconds),
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

        code_digest = hashlib.sha256(authorization_code.encode()).hexdigest()
        async with self._session.begin():
            stored_code = await self._native_auth_code_repository.find_for_update(
                code_digest
            )
            if stored_code is None:
                raise InvalidAuthorizationCodeError

            now = datetime.now(UTC)
            if stored_code.expires_at <= now:
                await self._native_auth_code_repository.delete(stored_code)
            else:
                if not secrets.compare_digest(
                    stored_code.code_challenge,
                    code_challenge,
                ):
                    raise InvalidAuthorizationCodeError

                access_token = self._token_service.issue_access_token(
                    stored_code.account_id
                )
                await self._native_auth_code_repository.delete(stored_code)
                return access_token

        raise InvalidAuthorizationCodeError
