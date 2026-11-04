from typing import Protocol


class OAuthClient(Protocol):
    """External OAuth provider; failures raise `domain.oauth.OAuthClientError`."""

    def create_authorization_url(self, *, state: str) -> str:
        """제공자 인증 화면 URL을 만듭니다."""
        ...

    async def get_user_id(self, *, code: str) -> str:
        """인증 코드를 검증하고 제공자 사용자 ID를 반환합니다."""
        ...
