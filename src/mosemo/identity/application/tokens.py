from datetime import datetime, timedelta
from uuid import UUID, uuid4

import jwt
from jwt import InvalidTokenError

JWT_ALGORITHM = "HS256"
REQUIRED_ACCESS_TOKEN_CLAIMS = [
    "sub",
    "iss",
    "aud",
    "iat",
    "exp",
    "jti",
    "token_type",
]


class InvalidAccessTokenError(Exception):
    pass


class TokenService:
    def __init__(
        self,
        *,
        secret_key: str,
        issuer: str,
        audience: str,
        access_token_ttl_seconds: int,
    ) -> None:
        self._secret_key = secret_key
        self._issuer = issuer
        self._audience = audience
        self._access_token_ttl = timedelta(seconds=access_token_ttl_seconds)

    def issue_access_token(self, account_id: UUID, *, now: datetime) -> str:
        return jwt.encode(
            {
                "sub": str(account_id),
                "iss": self._issuer,
                "aud": self._audience,
                "iat": now,
                "exp": now + self._access_token_ttl,
                "jti": str(uuid4()),
                "token_type": "access",
            },
            self._secret_key,
            algorithm=JWT_ALGORITHM,
        )

    def decode_access_token(self, token: str) -> UUID:
        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[JWT_ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": REQUIRED_ACCESS_TOKEN_CLAIMS,
                    "strict_aud": True,
                },
            )
            if payload["token_type"] != "access":
                raise InvalidAccessTokenError
            subject = payload["sub"]
            if not isinstance(subject, str):
                raise InvalidAccessTokenError
            return UUID(subject)
        except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise InvalidAccessTokenError from exc
