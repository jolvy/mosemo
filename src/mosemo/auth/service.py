import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.auth.kakao_client import KakaoClient, KakaoClientError
from mosemo.auth.pkce import create_code_challenge
from mosemo.auth.repository import NativeAuthCodeRepository
from mosemo.auth.tokens import TokenService
from mosemo.config import AuthConfig


class KakaoAuthenticationError(Exception):
    pass


class InvalidAuthorizationCodeError(Exception):
    pass


class AuthService:
    def __init__(
        self,
        *,
        kakao_client: KakaoClient,
        session: AsyncSession,
        account_repository: AccountRepository,
        native_auth_code_repository: NativeAuthCodeRepository,
        token_service: TokenService,
        config: AuthConfig,
    ) -> None:
        self._kakao_client = kakao_client
        self._session = session
        self._account_repository = account_repository
        self._native_auth_code_repository = native_auth_code_repository
        self._token_service = token_service
        self._config = config

    async def complete_kakao_login(
        self,
        *,
        code: str,
        code_challenge: str,
    ) -> str:
        try:
            provider_subject = await self._kakao_client.get_user_id(code=code)
        except KakaoClientError as exc:
            raise KakaoAuthenticationError from exc

        async with self._session.begin():
            account = await self._account_repository.find(
                provider=AccountProvider.KAKAO,
                provider_subject=provider_subject,
            )
            if account is None:
                account = self._account_repository.save(
                    provider=AccountProvider.KAKAO,
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
        stored_code = await self._native_auth_code_repository.find_for_update(
            code_digest
        )
        if stored_code is None:
            await self._session.rollback()
            raise InvalidAuthorizationCodeError

        now = datetime.now(UTC)
        if stored_code.expires_at <= now:
            await self._native_auth_code_repository.delete(stored_code)
            await self._session.commit()
            raise InvalidAuthorizationCodeError

        if not secrets.compare_digest(
            stored_code.code_challenge,
            code_challenge,
        ):
            await self._session.rollback()
            raise InvalidAuthorizationCodeError

        account_id = stored_code.account_id
        try:
            access_token = self._token_service.issue_access_token(account_id)
        except Exception:
            await self._session.rollback()
            raise

        await self._native_auth_code_repository.delete(stored_code)
        await self._session.commit()
        return access_token
