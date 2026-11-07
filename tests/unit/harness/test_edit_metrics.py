import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

E501 = (
    "src/a.py:1:89: E501 Line too long (90 > 88)\n"
    "src/a.py:2:89: E501 Line too long (91 > 88)"
)
GIT = {"harness_rev": "abc1234", "harness_dirty": False, "branch": "feat/x"}
MODEL_LINE = '{"type":"assistant","message":{"model":"claude-sonnet-5-5"}}'


def source(stop_metrics: ModuleType) -> object:
    return stop_metrics.Source(session="s1", agent="claude", model="claude-opus-5-5")


def failing_git(arguments: list[str]) -> str:
    raise OSError(arguments[0])


def test_edit_line_counts_remaining_violations(
    post_edit_feedback: ModuleType, stop_metrics: ModuleType
) -> None:
    line = post_edit_feedback.edit_line(
        ["src/a.py"], E501, source=source(stop_metrics), git=GIT
    )

    assert line == {
        "event": "edit",
        "session": "s1",
        "agent": "claude",
        "model": "claude-opus-5-5",
        "harness_rev": "abc1234",
        "harness_dirty": False,
        "branch": "feat/x",
        "rules": {"E501": 2},
    }


def test_edit_line_records_clean_edit_with_empty_rules(
    post_edit_feedback: ModuleType, stop_metrics: ModuleType
) -> None:
    line = post_edit_feedback.edit_line(
        ["src/a.py"], "", source=source(stop_metrics), git=GIT
    )

    assert line is not None
    assert line["rules"] == {}


def test_edit_line_skips_edit_without_python_target(
    post_edit_feedback: ModuleType, stop_metrics: ModuleType
) -> None:
    assert (
        post_edit_feedback.edit_line([], "", source=source(stop_metrics), git=GIT)
        is None
    )


def test_edit_line_skips_failed_ruff(
    post_edit_feedback: ModuleType, stop_metrics: ModuleType
) -> None:
    assert (
        post_edit_feedback.edit_line(
            ["src/a.py"], None, source=source(stop_metrics), git=GIT
        )
        is None
    )


def test_violation_notice_lists_remaining_violations(
    post_edit_feedback: ModuleType,
) -> None:
    assert post_edit_feedback.violation_notice(E501) == {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "Ruff 위반이 남아 있다. 저장소 규칙에 맞게 고쳐라.\n"
            + E501,
        }
    }
    assert post_edit_feedback.violation_notice("") is None
    assert post_edit_feedback.violation_notice(None) is None


def test_ruff_time_limit_leaves_room_for_recording(
    post_edit_feedback: ModuleType,
) -> None:
    assert post_edit_feedback.TIME_LIMIT == 7.0
    assert post_edit_feedback.RECORD_LIMIT == 1.5


def edit(post_edit_feedback: ModuleType, payload: dict[str, Any]) -> object:
    return post_edit_feedback.Edit(
        payload=payload, root=Path("/repo"), targets=["src/a.py"], output=E501
    )


def test_record_edit_writes_line_with_tail_model(
    post_edit_feedback: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "m.jsonl"
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(MODEL_LINE + "\n", encoding="utf-8")
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(log))
    monkeypatch.setenv("MOSEMO_AGENT", "claude")
    payload = {"session_id": "s1", "transcript_path": str(transcript)}

    post_edit_feedback.record_edit(
        edit(post_edit_feedback, payload), git=failing_git, deadline=float("inf")
    )

    line = json.loads(log.read_text(encoding="utf-8"))
    assert line["event"] == "edit"
    assert line["session"] == "s1"
    assert line["agent"] == "claude"
    assert line["model"] == "claude-sonnet-5-5"
    assert line["harness_dirty"] is True
    assert line["branch"] is None
    assert line["rules"] == {"E501": 2}


def test_record_edit_leaves_model_unknown_after_deadline(
    post_edit_feedback: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "m.jsonl"
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(MODEL_LINE + "\n", encoding="utf-8")
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(log))
    payload = {"transcript_path": str(transcript)}

    post_edit_feedback.record_edit(
        edit(post_edit_feedback, payload), git=failing_git, deadline=0.0
    )

    line = json.loads(log.read_text(encoding="utf-8"))
    assert line["model"] is None
    assert line["session"] == ""


def test_record_edit_writes_nothing_when_ruff_failed(
    post_edit_feedback: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "m.jsonl"
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(log))
    failed = post_edit_feedback.Edit(
        payload={}, root=Path("/repo"), targets=["src/a.py"], output=None
    )

    post_edit_feedback.record_edit(failed, git=failing_git, deadline=float("inf"))

    assert not log.exists()


def test_record_edit_failure_does_not_change_notice(
    post_edit_feedback: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing" / "m.jsonl"
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(missing))
    before = post_edit_feedback.violation_notice(E501)

    post_edit_feedback.record_edit(
        edit(post_edit_feedback, {"session_id": "s"}),
        git=failing_git,
        deadline=float("inf"),
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert captured.err.startswith("지표 로그 기록 실패")
    assert post_edit_feedback.violation_notice(E501) == before
