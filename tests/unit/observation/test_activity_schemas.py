from datetime import datetime, timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from mosemo.observation.domain.activities import CollectionState, RecordType
from mosemo.observation.presentation.activities.schemas import (
    ActivityObservation,
    ActivityRecord,
    CapturedText,
    CollectionStateChanged,
    DetailedActivityContext,
    OpaqueActivityContext,
    TimelineTimestamp,
)
from mosemo.shared.domain.timezones import Timezone

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


@pytest.mark.parametrize(
    ("timezone_id", "expected"),
    [
        ("Asia/Seoul", Timezone.ASIA_SEOUL),
        ("America/New_York", Timezone.AMERICA_NEW_YORK),
        ("UTC", Timezone.UTC),
    ],
)
def test_activity_record_parses_supported_timezone(
    timezone_id: str, expected: Timezone
) -> None:
    record = activity_record_adapter.validate_python(
        {
            "recordType": "activity_observation",
            **record_fields(412),
            "timezoneId": timezone_id,
            "context": {"kind": "opaque"},
        }
    )

    assert record.timezone_id is expected


def test_activity_record_rejects_unsupported_timezone() -> None:
    with pytest.raises(ValidationError, match="timezoneId"):
        activity_record_adapter.validate_python(
            {
                "recordType": "activity_observation",
                **record_fields(412),
                "timezoneId": "Europe/Paris",
                "context": {"kind": "opaque"},
            }
        )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("active", CollectionState.ACTIVE),
        ("suspended", CollectionState.SUSPENDED),
    ],
)
def test_collection_state_round_trips_as_string(
    state: str, expected: CollectionState
) -> None:
    record = activity_record_adapter.validate_python(
        {
            "recordType": "collection_state_changed",
            **record_fields(415),
            "state": state,
            "reason": "screen_locked",
        }
    )

    assert isinstance(record, CollectionStateChanged)
    assert record.state is expected
    assert record.model_dump(mode="json", by_alias=True)["state"] == state


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
    assert detailed_record.record_type is RecordType.ACTIVITY_OBSERVATION
    assert state_record.record_type is RecordType.COLLECTION_STATE_CHANGED
    assert state_record.state is CollectionState.SUSPENDED
    assert state_record.model_dump(mode="json", by_alias=True)["state"] == "suspended"
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


def test_captured_text_rejects_byte_length_when_not_truncated() -> None:
    with pytest.raises(
        ValidationError,
        match="original_byte_length is only allowed when truncated is true",
    ):
        CapturedText.model_validate(
            {
                "status": "captured",
                "value": "Mosemo",
                "truncated": False,
                "originalByteLength": 12,
            }
        )


def test_timeline_timestamp_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        TypeAdapter(TimelineTimestamp).validate_python(
            datetime.fromisoformat("2026-09-07T10:15:30")
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


def detailed_context(**overrides: object) -> dict[str, object]:
    return {
        "kind": "detailed",
        "app": {
            "bundleId": {"status": "captured", "value": "com.apple.Safari"},
            "name": overrides.get(
                "app_name", {"status": "captured", "value": "Safari"}
            ),
        },
        "window": overrides.get(
            "window",
            {"status": "captured", "title": {"status": "captured", "value": "Docs"}},
        ),
        "web": {
            "kind": "browser",
            "tabTitle": overrides.get("tab_title", {"status": "absent"}),
            "url": {"status": "absent"},
        },
    }


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(
            {
                "recordType": "activity_observation",
                "context": detailed_context(
                    app_name={"status": "captured", "value": "Saf\x00ari"}
                ),
            },
            id="captured_string_value",
        ),
        pytest.param(
            {
                "recordType": "activity_observation",
                "context": detailed_context(
                    window={
                        "status": "captured",
                        "title": {"status": "captured", "value": "Do\x00cs"},
                    }
                ),
            },
            id="captured_text_value",
        ),
        pytest.param(
            {
                "recordType": "activity_observation",
                "context": detailed_context(
                    app_name={"status": "unavailable", "reason": "denied\x00"}
                ),
            },
            id="unavailable_value_reason",
        ),
        pytest.param(
            {
                "recordType": "activity_observation",
                "context": detailed_context(
                    tab_title={"status": "redacted", "reason": "\x00private"}
                ),
            },
            id="redacted_value_reason",
        ),
        pytest.param(
            {
                "recordType": "activity_observation",
                "context": detailed_context(
                    window={"status": "unavailable", "reason": "ax\x00denied"}
                ),
            },
            id="unavailable_window_reason",
        ),
        pytest.param(
            {
                "recordType": "collection_state_changed",
                "state": "suspended",
                "reason": "screen\x00locked",
            },
            id="collection_state_reason",
        ),
    ],
)
def test_activity_record_rejects_nul_in_any_string(record: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        activity_record_adapter.validate_python({**record_fields(412), **record})


def test_activity_record_rejects_sequence_beyond_bigint() -> None:
    def observation(sequence: int) -> dict[str, object]:
        return {
            "recordType": "activity_observation",
            **record_fields(412),
            "sequence": sequence,
            "context": {"kind": "opaque"},
        }

    record = activity_record_adapter.validate_python(observation(2**63 - 1))
    assert record.sequence == 2**63 - 1
    with pytest.raises(ValidationError, match="sequence"):
        activity_record_adapter.validate_python(observation(2**63))


@pytest.mark.parametrize(
    ("offset", "accepted"),
    [(1440, True), (-1440, True), (1441, False), (-1441, False)],
)
def test_activity_record_rejects_utc_offset_out_of_range(
    offset: int, accepted: bool
) -> None:
    observation = {
        "recordType": "activity_observation",
        **record_fields(412),
        "utcOffsetMinutes": offset,
        "context": {"kind": "opaque"},
    }
    if accepted:
        record = activity_record_adapter.validate_python(observation)
        assert record.utc_offset_minutes == offset
        return
    with pytest.raises(ValidationError, match="utcOffsetMinutes"):
        activity_record_adapter.validate_python(observation)
