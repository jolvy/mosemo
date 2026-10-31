from uuid import UUID

from mosemo.activities.service import activity_timeline_lock_key


def test_activity_timeline_lock_key_is_pinned() -> None:
    # Computed at 48f116f; a changed key breaks lock serialization across deploys.
    account_id = UUID("01890a5d-ac96-774b-bcce-b302099a8057")

    assert activity_timeline_lock_key(account_id) == 6618789885453005023
