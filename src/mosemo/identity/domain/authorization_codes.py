import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from mosemo.shared.domain.aggregates import AggregateRoot


def authorization_code_digest(authorization_code: str) -> str:
    return hashlib.sha256(authorization_code.encode()).hexdigest()


@dataclass(eq=False, repr=False, kw_only=True)
class NativeAuthCode(AggregateRoot):
    """One-time authorization code that the macOS app exchanges for a token."""

    code_digest: str
    account_id: UUID
    code_challenge: str
    expires_at: datetime
    created_at: datetime = field(init=False)

    @classmethod
    def issue(
        cls,
        *,
        account_id: UUID,
        code_challenge: str,
        now: datetime,
        ttl: timedelta,
    ) -> tuple[NativeAuthCode, str]:
        authorization_code = secrets.token_urlsafe(32)
        code = cls(
            code_digest=authorization_code_digest(authorization_code),
            account_id=account_id,
            code_challenge=code_challenge,
            expires_at=now + ttl,
        )
        return code, authorization_code

    def is_expired(self, *, now: datetime) -> bool:
        return self.expires_at <= now

    def matches(self, code_challenge: str) -> bool:
        return secrets.compare_digest(self.code_challenge, code_challenge)
