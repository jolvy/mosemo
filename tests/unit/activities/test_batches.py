from uuid import uuid4

import pytest
from pydantic import ValidationError

from mosemo.activities.schemas import ActivityBatchRequest


def batch_payload():
    return {
        "batchId": str(uuid4()),
        "deviceId": str(uuid4()),
        "records": [
            {
                "eventId": str(uuid4()),
                "sequence": 0,
                "recordType": "activity_observation",
                "observedAt": "2026-09-14T00:00:00.123456Z",
                "timezoneId": "Asia/Seoul",
                "utcOffsetMinutes": 540,
                "context": {"kind": "opaque"},
            }
        ],
    }


def test_batch_injects_validated_device_without_changing_observation():
    payload = batch_payload()
    batch = ActivityBatchRequest.model_validate(payload)
    records = batch.activity_records()
    assert str(records[0].device_id) == payload["deviceId"]
    assert records[0].observed_at.microsecond == 123456
    assert "deviceId" not in batch.model_dump(by_alias=True)["records"][0]


@pytest.mark.parametrize("change", ["empty", "count", "device", "account", "suspended"])
def test_batch_validation_is_at_request_boundary(change):
    payload = batch_payload()
    if change == "empty":
        payload["records"] = []
    elif change == "count":
        payload["records"] *= 201
    elif change == "device":
        payload["records"][0]["deviceId"] = payload["deviceId"]
    elif change == "account":
        payload["accountId"] = str(uuid4())
    else:
        payload["records"][0].pop("context")
        payload["records"][0].update(
            recordType="collection_state_changed",
            state="suspended",
            reason="locked",
            focusSessionId=str(uuid4()),
        )
    with pytest.raises(ValidationError):
        ActivityBatchRequest.model_validate(payload)
