from datetime import timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from mosemo.activities.schemas import (
    ActivityObservation,
    ActivityRecord,
    CapturedText,
    CollectionStateChanged,
    DetailedActivityContext,
    OpaqueActivityContext,
)

activity_record_adapter = TypeAdapter(ActivityRecord)


def record_fields(sequence: int) -> dict[str, object]:
    return {
        "deviceId": "30000000-0000-0000-0000-000000000000",
        "eventId": f"00000000-0000-0000-0000-{sequence:012d}",
        "sequence": sequence,
        "observedAt": "2026-09-07T10:15:30Z",
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
    }


def test_activity_record_parses_each_record_and_context_variant() -> None:
    records = [
        activity_record_adapter.validate_python(record)
        for record in [
            {
                "recordType": "activity_observation",
                **record_fields(412),
                "context": {
                    "kind": "detailed",
                    "app": {
                        "bundleId": {
                            "status": "captured",
                            "value": "com.apple.Safari",
                        },
                        "name": {
                            "status": "captured",
                            "value": "Safari",
                        },
                    },
                    "window": {
                        "status": "captured",
                        "title": {
                            "status": "captured",
                            "value": "프로젝트 설계 문서",
                        },
                    },
                    "web": {
                        "kind": "browser",
                        "tabTitle": {
                            "status": "captured",
                            "value": "Mosemo",
                        },
                        "url": {
                            "status": "captured",
                            "value": "https://example.com/work",
                        },
                    },
                },
            },
            {
                "recordType": "activity_observation",
                **record_fields(413),
                "context": {"kind": "opaque"},
            },
            {
                "recordType": "collection_state_changed",
                **record_fields(414),
                "state": "suspended",
                "reason": "screen_locked",
            },
        ]
    ]

    detailed_record = records[0]
    opaque_record = records[1]
    state_record = records[2]

    assert isinstance(detailed_record, ActivityObservation)
    assert isinstance(detailed_record.context, DetailedActivityContext)
    assert isinstance(opaque_record, ActivityObservation)
    assert isinstance(opaque_record.context, OpaqueActivityContext)
    assert isinstance(state_record, CollectionStateChanged)
    assert detailed_record.observed_at.utcoffset() == timedelta(0)


def test_activity_record_rejects_identifiers_in_opaque_context() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        activity_record_adapter.validate_python(
            {
                "recordType": "activity_observation",
                **record_fields(412),
                "context": {
                    "kind": "opaque",
                    "bundleId": "com.example.secret",
                },
            }
        )


def test_activity_record_requires_truncated_text_byte_length() -> None:
    with pytest.raises(
        ValidationError,
        match="original_byte_length is required when truncated is true",
    ):
        activity_record_adapter.validate_python(
            {
                "recordType": "activity_observation",
                **record_fields(412),
                "context": {
                    "kind": "detailed",
                    "app": {
                        "bundleId": {"status": "absent"},
                        "name": {
                            "status": "captured",
                            "value": "Safari",
                        },
                    },
                    "window": {"status": "absent"},
                    "web": {
                        "kind": "browser",
                        "tabTitle": {
                            "status": "captured",
                            "value": "truncated",
                            "truncated": True,
                        },
                        "url": {
                            "status": "redacted",
                            "reason": "length_exceeded",
                        },
                    },
                },
            }
        )


def test_captured_text_requires_original_value_to_be_longer_than_prefix() -> None:
    with pytest.raises(
        ValidationError,
        match="original_byte_length must exceed the captured value byte length",
    ):
        CapturedText.model_validate(
            {
                "status": "captured",
                "value": "Mosemo",
                "truncated": True,
                "originalByteLength": 6,
            }
        )


def test_activity_record_requires_utc_observed_at() -> None:
    with pytest.raises(
        ValidationError,
        match="observation timestamps must be RFC 3339 UTC strings ending in Z",
    ):
        activity_record_adapter.validate_python(
            {
                "recordType": "collection_state_changed",
                **record_fields(412),
                "observedAt": "2026-09-07T19:15:30+09:00",
                "state": "active",
                "reason": "collector_started",
            }
        )
