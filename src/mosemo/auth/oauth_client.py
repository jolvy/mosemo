from typing import Protocol


class OAuthClientError(Exception):
    """외부 OAuth 제공자 인증에 실패했습니다."""


class OAuthClient(Protocol):
    async def get_user_id(self, *, code: str) -> str:
        """인증 코드를 검증하고 제공자 사용자 ID를 반환합니다."""
