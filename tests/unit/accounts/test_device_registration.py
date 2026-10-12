from dataclasses import FrozenInstanceError, asdict
from uuid import UUID

import pytest

from mosemo.accounts.device_registration import (
    DeviceRegistration,
    decide_device_registration,
)
from mosemo.accounts.models import Account

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
KEY = UUID("00000000-0000-0000-0000-000000000002")
CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000003")


def test_account_selects_new_device_owned_by_itself() -> None:
    account = Account(account_id=ACCOUNT_ID)

    decision = account.register_device(
        idempotency_key=KEY,
        candidate_id=CANDIDATE_ID,
        existing_registration=None,
    )

    assert decision.should_create is True
    assert decision.registration.device_id == CANDIDATE_ID
    assert decision.registration.account_id == ACCOUNT_ID
    assert decision.registration.idempotency_key == KEY


def test_account_retry_selects_original_device_instead_of_candidate() -> None:
    account = Account(account_id=ACCOUNT_ID)
    original_id = UUID("00000000-0000-0000-0000-000000000004")
    existing = DeviceRegistration(original_id, ACCOUNT_ID, KEY)

    decision = account.register_device(
        idempotency_key=KEY,
        candidate_id=CANDIDATE_ID,
        existing_registration=existing,
    )

    assert decision.should_create is False
    assert decision.registration == DeviceRegistration(original_id, ACCOUNT_ID, KEY)


def test_account_rejects_registration_owned_by_another_account() -> None:
    account = Account(account_id=ACCOUNT_ID)
    other_account_id = UUID("00000000-0000-0000-0000-000000000005")
    existing = DeviceRegistration(CANDIDATE_ID, other_account_id, KEY)

    with pytest.raises(ValueError, match="registration belongs to another account"):
        account.register_device(
            idempotency_key=KEY,
            candidate_id=CANDIDATE_ID,
            existing_registration=existing,
        )


def test_account_rejects_registration_for_another_idempotency_key() -> None:
    account = Account(account_id=ACCOUNT_ID)
    other_key = UUID("00000000-0000-0000-0000-000000000006")
    existing = DeviceRegistration(CANDIDATE_ID, ACCOUNT_ID, other_key)

    with pytest.raises(ValueError, match="registration has another idempotency key"):
        account.register_device(
            idempotency_key=KEY,
            candidate_id=CANDIDATE_ID,
            existing_registration=existing,
        )


@pytest.mark.parametrize(
    "existing",
    [None, DeviceRegistration(CANDIDATE_ID, ACCOUNT_ID, KEY)],
)
def test_registration_policy_is_deterministic_and_preserves_input(
    existing: DeviceRegistration | None,
) -> None:
    before = asdict(existing) if existing is not None else None
    decisions = [
        decide_device_registration(
            account_id=ACCOUNT_ID,
            idempotency_key=KEY,
            candidate_id=CANDIDATE_ID,
            existing_registration=existing,
        )
        for _ in range(2)
    ]

    assert decisions[0] == decisions[1]
    assert (asdict(existing) if existing is not None else None) == before
    for target, attribute, value in (
        (decisions[0].registration, "device_id", UUID(int=99)),
        (decisions[0], "should_create", False),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, attribute, value)
