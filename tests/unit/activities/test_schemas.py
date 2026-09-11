from datetime import timedelta

import pytest
from pydantic import ValidationError

from mosemo.activities.schemas import (
    ActivityBatchRequest,
    ActivityObservation,
    CapturedText,
    CollectionStateChanged,
    DetailedActivityContext,
    OpaqueActivityContext,
)


def record_fields(sequence: int) -> dict[str, object]:
    return {
        "event_id": f"00000000-0000-0000-0000-{sequence:012d}",
        "sequence": sequence,
        "observed_at": "2026-09-07T10:15:30Z",
        "timezone_id": "Asia/Seoul",
        "utc_offset_minutes": 540,
        "clock_epoch_id": "10000000-0000-0000-0000-000000000000",
        "monotonic_ns": 8_920_033_000_000 + sequence,
    }


def test_activity_batch_request_parses_each_record_and_context_variant() -> None:
    request = ActivityBatchRequest.model_validate(
        {
            "batch_id": "20000000-0000-0000-0000-000000000000",
            "device_registration_id": ("30000000-0000-0000-0000-000000000000"),
            "collection_stream_id": ("40000000-0000-0000-0000-000000000000"),
            "records": [
                {
                    "record_type": "activity_observation",
                    **record_fields(412),
                    "context": {
                        "kind": "detailed",
                        "app": {
                            "bundle_id": {
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
                            "tab_title": {
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
                    "record_type": "activity_observation",
                    **record_fields(413),
                    "context": {"kind": "opaque"},
                },
                {
                    "record_type": "collection_state_changed",
                    **record_fields(414),
                    "state": "suspended",
                    "reason": "screen_locked",
                },
            ],
        }
    )

    detailed_record = request.records[0]
    opaque_record = request.records[1]
    state_record = request.records[2]

    assert isinstance(detailed_record, ActivityObservation)
    assert isinstance(detailed_record.context, DetailedActivityContext)
    assert isinstance(opaque_record, ActivityObservation)
    assert isinstance(opaque_record.context, OpaqueActivityContext)
    assert isinstance(state_record, CollectionStateChanged)
    assert detailed_record.observed_at.utcoffset() == timedelta(0)


def test_activity_batch_request_rejects_identifiers_in_opaque_context() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ActivityBatchRequest.model_validate(
            {
                "batch_id": "20000000-0000-0000-0000-000000000000",
                "device_registration_id": ("30000000-0000-0000-0000-000000000000"),
                "collection_stream_id": ("40000000-0000-0000-0000-000000000000"),
                "records": [
                    {
                        "record_type": "activity_observation",
                        **record_fields(412),
                        "context": {
                            "kind": "opaque",
                            "bundle_id": "com.example.secret",
                        },
                    }
                ],
            }
        )


def test_activity_batch_request_requires_truncated_text_byte_length() -> None:
    with pytest.raises(
        ValidationError,
        match="original_byte_length is required when truncated is true",
    ):
        ActivityBatchRequest.model_validate(
            {
                "batch_id": "20000000-0000-0000-0000-000000000000",
                "device_registration_id": ("30000000-0000-0000-0000-000000000000"),
                "collection_stream_id": ("40000000-0000-0000-0000-000000000000"),
                "records": [
                    {
                        "record_type": "activity_observation",
                        **record_fields(412),
                        "context": {
                            "kind": "detailed",
                            "app": {
                                "bundle_id": {"status": "absent"},
                                "name": {
                                    "status": "captured",
                                    "value": "Safari",
                                },
                            },
                            "window": {"status": "absent"},
                            "web": {
                                "kind": "browser",
                                "tab_title": {
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
                ],
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
                "original_byte_length": 6,
            }
        )


def test_activity_batch_request_requires_utc_observed_at() -> None:
    with pytest.raises(ValidationError, match="observed_at must use UTC"):
        ActivityBatchRequest.model_validate(
            {
                "batch_id": "20000000-0000-0000-0000-000000000000",
                "device_registration_id": ("30000000-0000-0000-0000-000000000000"),
                "collection_stream_id": ("40000000-0000-0000-0000-000000000000"),
                "records": [
                    {
                        "record_type": "collection_state_changed",
                        **record_fields(412),
                        "observed_at": "2026-09-07T19:15:30+09:00",
                        "state": "active",
                        "reason": "collector_started",
                    }
                ],
            }
        )
