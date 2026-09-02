from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from jwt import InvalidTokenError

from mosemo.config import AuthConfig

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
    def __init__(self, config: AuthConfig) -> None:
        self._config = config

    def issue_access_token(self, account_id: UUID) -> str:
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(
            seconds=self._config.access_token_ttl_seconds
        )
        return jwt.encode(
            {
                "sub": str(account_id),
                "iss": self._config.jwt_issuer,
                "aud": self._config.jwt_audience,
                "iat": issued_at,
                "exp": expires_at,
                "jti": str(uuid4()),
                "token_type": "access",
            },
            self._config.jwt_secret_key.get_secret_value(),
            algorithm=JWT_ALGORITHM,
        )

    def decode_access_token(self, token: str) -> UUID:
        try:
            payload = jwt.decode(
                token,
                self._config.jwt_secret_key.get_secret_value(),
                algorithms=[JWT_ALGORITHM],
                audience=self._config.jwt_audience,
                issuer=self._config.jwt_issuer,
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
