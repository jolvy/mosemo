from uuid import UUID

import pytest

from mosemo.platform.database.persistence import account_lock_key


@pytest.mark.parametrize(
    ("account_id", "expected"),
    [
        (UUID("00000000-0000-0000-0000-000000000001"), 4853908014634014033),
        (UUID("0190f0c4-1111-7000-8000-000000000000"), -7343975160835373934),
    ],
)
def test_account_lock_key_keeps_the_timeline_lock_bytes(
    account_id: UUID, expected: int
) -> None:
    # The key observation took before the move; identity must take the same lock.
    assert account_lock_key(account_id) == expected
