from collections.abc import Mapping

from fastapi import status


class MosemoApiException(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        detail: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = dict(headers) if headers is not None else None


class InvalidAccessTokenApiException(MosemoApiException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )


class InvalidAuthorizationCodeApiException(MosemoApiException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired authorization code",
        )
