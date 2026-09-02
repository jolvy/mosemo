from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from mosemo.auth.tokens import InvalidAccessTokenError, TokenService
from mosemo.config import Config


def test_issue_and_decode_access_token(config: Config) -> None:
    service = TokenService(config.auth)
    account_id = uuid4()

    token = service.issue_access_token(account_id)
    payload = jwt.decode(token, options={"verify_signature": False})

    assert service.decode_access_token(token) == account_id
    assert payload["sub"] == str(account_id)
    assert payload["iss"] == "mosemo"
    assert payload["aud"] == "mosemo-api"
    assert payload["token_type"] == "access"
    assert payload["exp"] - payload["iat"] == 86_400
    assert isinstance(payload["jti"], str)


def encode_token(config: Config, **overrides) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid4()),
        "iss": config.auth.jwt_issuer,
        "aud": config.auth.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": str(uuid4()),
        "token_type": "access",
    }
    payload.update(overrides)
    return jwt.encode(
        payload,
        config.auth.jwt_secret_key.get_secret_value(),
        algorithm="HS256",
    )


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        jwt.encode(
            {"sub": str(uuid4())},
            "a-different-secret-key-that-is-long-enough",
            algorithm="HS256",
        ),
    ],
)
def test_decode_rejects_malformed_or_forged_token(
    config: Config,
    token: str,
) -> None:
    with pytest.raises(InvalidAccessTokenError):
        TokenService(config.auth).decode_access_token(token)


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": datetime.now(UTC) - timedelta(seconds=1)},
        {"iss": "another-issuer"},
        {"aud": "another-audience"},
        {"token_type": "refresh"},
        {"sub": "not-a-uuid"},
    ],
)
def test_decode_rejects_invalid_claims(config: Config, overrides: dict) -> None:
    token = encode_token(config, **overrides)

    with pytest.raises(InvalidAccessTokenError):
        TokenService(config.auth).decode_access_token(token)


def test_decode_rejects_missing_required_claim(config: Config) -> None:
    token = encode_token(config)
    payload = jwt.decode(token, options={"verify_signature": False})
    del payload["jti"]
    token = jwt.encode(
        payload,
        config.auth.jwt_secret_key.get_secret_value(),
        algorithm="HS256",
    )

    with pytest.raises(InvalidAccessTokenError):
        TokenService(config.auth).decode_access_token(token)
