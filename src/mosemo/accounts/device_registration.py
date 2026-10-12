"""Account-owned registration values and deterministic selection policy."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class DeviceRegistration:
    device_id: UUID
    account_id: UUID
    idempotency_key: UUID


@dataclass(frozen=True)
class DeviceRegistrationDecision:
    registration: DeviceRegistration
    should_create: bool


def decide_device_registration(
    *,
    account_id: UUID,
    idempotency_key: UUID,
    candidate_id: UUID,
    existing_registration: DeviceRegistration | None,
) -> DeviceRegistrationDecision:
    if existing_registration is not None:
        if existing_registration.account_id != account_id:
            raise ValueError("registration belongs to another account")
        if existing_registration.idempotency_key != idempotency_key:
            raise ValueError("registration has another idempotency key")
        return DeviceRegistrationDecision(existing_registration, should_create=False)
    return DeviceRegistrationDecision(
        registration=DeviceRegistration(candidate_id, account_id, idempotency_key),
        should_create=True,
    )
