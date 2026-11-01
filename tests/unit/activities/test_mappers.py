from datetime import UTC, datetime
from uuid import uuid4

from pydantic import TypeAdapter

from mosemo.activities.enums import RecordType
from mosemo.activities.mappers import ActivityRecordMapper
from mosemo.activities.schemas import ActivityRecord
from mosemo.timezones import Timezone

activity_record_adapter = TypeAdapter(ActivityRecord)


def envelope() -> dict[str, object]:
    return {
        "deviceId": str(uuid4()),
        "eventId": str(uuid4()),
        "focusSessionId": str(uuid4()),
        "sequence": 3,
        "observedAt": "2026-09-14T00:00:00Z",
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
    }


def test_to_entity_copies_envelope_fields() -> None:
    request = activity_record_adapter.validate_python(
        {
            **envelope(),
            "recordType": "activity_observation",
            "context": {"kind": "opaque"},
        }
    )

    entity = ActivityRecordMapper().to_entity(request)

    assert entity.event_id == request.event_id
    assert entity.device_id == request.device_id
    assert entity.focus_session_id == request.focus_session_id
    assert entity.sequence == 3
    assert entity.record_type is RecordType.ACTIVITY_OBSERVATION
    assert entity.observed_at == datetime(2026, 9, 14, tzinfo=UTC)
    assert entity.timezone_id is Timezone.ASIA_SEOUL
    assert entity.utc_offset_minutes == 540
    assert entity.payload == {"context": {"kind": "opaque"}}
    assert entity.received_at is None


def test_to_entity_keeps_nested_public_field_names_in_observation_payload() -> None:
    request = activity_record_adapter.validate_python(
        {
            **envelope(),
            "recordType": "activity_observation",
            "context": {
                "kind": "detailed",
                "app": {
                    "bundleId": {"status": "captured", "value": "com.apple.Safari"},
                    "name": {"status": "captured", "value": "Safari"},
                },
                "window": {
                    "status": "captured",
                    "title": {
                        "status": "captured",
                        "value": "Mosemo",
                        "truncated": True,
                        "originalByteLength": 12,
                    },
                },
                "web": {"kind": "not_applicable"},
            },
        }
    )

    entity = ActivityRecordMapper().to_entity(request)

    assert entity.payload == {
        "context": {
            "kind": "detailed",
            "app": {
                "bundleId": {
                    "status": "captured",
                    "value": "com.apple.Safari",
                },
                "name": {"status": "captured", "value": "Safari"},
            },
            "window": {
                "status": "captured",
                "title": {
                    "status": "captured",
                    "value": "Mosemo",
                    "truncated": True,
                    "originalByteLength": 12,
                },
            },
            "web": {"kind": "not_applicable"},
        }
    }


def test_to_entity_stores_collection_state_and_reason_in_payload() -> None:
    request = activity_record_adapter.validate_python(
        {
            **envelope(),
            "recordType": "collection_state_changed",
            "state": "active",
            "reason": "screen_unlocked",
        }
    )

    entity = ActivityRecordMapper().to_entity(request)

    assert entity.record_type is RecordType.COLLECTION_STATE_CHANGED
    assert entity.payload == {"state": "active", "reason": "screen_unlocked"}
