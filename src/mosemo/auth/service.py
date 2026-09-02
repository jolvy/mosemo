from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.auth.kakao_client import KakaoClient, KakaoClientError


class KakaoAuthenticationError(Exception):
    pass


class AuthService:
    def __init__(
        self,
        *,
        kakao_client: KakaoClient,
        session: AsyncSession,
        account_repository: AccountRepository,
    ) -> None:
        self._kakao_client = kakao_client
        self._session = session
        self._account_repository = account_repository

    async def authenticate_kakao(self, *, code: str) -> Account:
        try:
            provider_subject = await self._kakao_client.get_user_id(code=code)
        except KakaoClientError as exc:
            raise KakaoAuthenticationError from exc

        account = await self._account_repository.find(
            provider=AccountProvider.KAKAO,
            provider_subject=provider_subject,
        )
        if account:
            return account

        account = self._account_repository.save(
            provider=AccountProvider.KAKAO,
            provider_subject=provider_subject,
        )

        await self._session.commit()
        await self._session.refresh(account)

        return account
