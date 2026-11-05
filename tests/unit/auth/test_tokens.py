from datetime import timedelta
from uuid import uuid4

import jwt
import pytest

from mosemo.identity.application.tokens import InvalidAccessTokenError
from mosemo.identity.bootstrap import IdentityConfig
from mosemo.platform.clock import utc_now
from tests.support.identity import token_service


def test_issue_and_decode_access_token(identity_config: IdentityConfig) -> None:
    service = token_service(identity_config)
    account_id = uuid4()

    token = service.issue_access_token(account_id, now=utc_now())
    payload = jwt.decode(token, options={"verify_signature": False})

    assert service.decode_access_token(token) == account_id
    assert payload["sub"] == str(account_id)
    assert payload["iss"] == "mosemo"
    assert payload["aud"] == "mosemo-api"
    assert payload["token_type"] == "access"
    assert payload["exp"] - payload["iat"] == 86_400
    assert isinstance(payload["jti"], str)


def encode_token(identity_config: IdentityConfig, **overrides) -> str:
    now = utc_now()
    payload = {
        "sub": str(uuid4()),
        "iss": identity_config.jwt_issuer,
        "aud": identity_config.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": str(uuid4()),
        "token_type": "access",
    }
    payload.update(overrides)
    return jwt.encode(
        payload,
        identity_config.jwt_secret_key,
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
    identity_config: IdentityConfig,
    token: str,
) -> None:
    with pytest.raises(InvalidAccessTokenError):
        token_service(identity_config).decode_access_token(token)


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": utc_now() - timedelta(seconds=1)},
        {"iss": "another-issuer"},
        {"aud": "another-audience"},
        {"token_type": "refresh"},
        {"sub": "not-a-uuid"},
    ],
)
def test_decode_rejects_invalid_claims(
    identity_config: IdentityConfig, overrides: dict
) -> None:
    token = encode_token(identity_config, **overrides)

    with pytest.raises(InvalidAccessTokenError):
        token_service(identity_config).decode_access_token(token)


def test_decode_rejects_missing_required_claim(identity_config: IdentityConfig) -> None:
    token = encode_token(identity_config)
    payload = jwt.decode(token, options={"verify_signature": False})
    del payload["jti"]
    token = jwt.encode(
        payload,
        identity_config.jwt_secret_key,
        algorithm="HS256",
    )

    with pytest.raises(InvalidAccessTokenError):
        token_service(identity_config).decode_access_token(token)
