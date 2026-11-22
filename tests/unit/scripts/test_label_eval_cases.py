import hashlib
import json
import os
import random
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any, Self
from uuid import UUID

import pytest

KEYS = ("app_bundle_id", "app_name", "window_title", "tab_title", "web_host")
PERSONA = """
name = "{name}"
labels = ["공부", "휴식"]
[[rules]]
app_name = ["{study_app}"]
label = "공부"
[[rules]]
app_name = ["Notion"]
label = "null"
"""


def _activity(number: int, ended_at: str, **values: str) -> dict[str, Any]:
    fields = {key: values.get(key) for key in KEYS}
    return {
        "activity_id": str(UUID(int=number)),
        "account_id": str(UUID(int=999)),
        "version": f"v{number}",
        "ended_at": ended_at,
        "activity": "\n".join(f"{k}: {v}" for k, v in fields.items() if v is not None),
        "fields": fields,
    }


def _minute(minute: int) -> str:
    return f"2026-01-01T00:{minute:02d}:00Z"


def _persona(module: ModuleType, name: str = "p", study_app: str = "Code"):
    text = PERSONA.format(name=name, study_app=study_app)
    return module.parse_persona(tomllib.loads(text))


def _by_activity(cases: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {UUID(str(case["activity_id"])).int: case for case in cases}


def test_examples_are_strictly_earlier_cases_latest_first(
    label_eval_cases: ModuleType,
) -> None:
    activities = [
        _activity(number, _minute(number), app_name="Code") for number in range(1, 12)
    ]
    # Same end as activity 11 but after it in (ended_at, activity_id) order.
    activities.append(_activity(12, _minute(11), app_name="Code"))
    persona = _persona(label_eval_cases)

    cases = _by_activity(
        label_eval_cases.build_cases(list(reversed(activities)), [persona])
    )

    assert cases[1]["examples"] == []
    assert [UUID(e["activity_id"]).int for e in cases[3]["examples"]] == [2, 1]
    assert [UUID(e["activity_id"]).int for e in cases[11]["examples"]] == list(
        range(10, 2, -1)
    )
    assert [UUID(e["activity_id"]).int for e in cases[12]["examples"]] == list(
        range(11, 3, -1)
    )
    assert cases[3]["examples"][0] == {
        "activity_id": str(UUID(int=2)),
        "activity": "app_name: Code",
        "label_id": persona.label_id("공부"),
    }


def test_case_rows_follow_the_contract_and_include_null_examples(
    label_eval_cases: ModuleType,
) -> None:
    persona = _persona(label_eval_cases)
    activities = [
        _activity(1, _minute(1), app_name="Notion"),
        _activity(2, _minute(2), app_name="Code", web_host="a.example"),
    ]

    first, second = label_eval_cases.build_cases(activities, [persona])

    assert first["gold"] is None
    assert second == {
        "case_id": f"p/{UUID(int=2)}",
        "persona": "p",
        "activity_id": str(UUID(int=2)),
        "activity": "app_name: Code\nweb_host: a.example",
        "fields": activities[1]["fields"],
        "labels": [
            {"label_id": persona.label_id("공부"), "name": "공부"},
            {"label_id": persona.label_id("휴식"), "name": "휴식"},
        ],
        "examples": [
            {
                "activity_id": str(UUID(int=1)),
                "activity": "app_name: Notion",
                "label_id": None,
            }
        ],
        "gold": persona.label_id("공부"),
        "tags": ["cold_start", "new_host"],
    }


def test_tags_follow_their_definitions(label_eval_cases: ModuleType) -> None:
    study = _persona(label_eval_cases, "study", study_app="Code")
    other = _persona(label_eval_cases, "other", study_app="Slack")
    hosts = ["a.example", "a.example", "b.example", None, "b.example", "c.example"]
    activities = [
        _activity(number, _minute(number), app_name="Code", web_host=host)
        if host
        else _activity(number, _minute(number), app_name="Notion")
        for number, host in enumerate(hosts, start=1)
    ]
    activities.append(_activity(7, _minute(7), app_name="Finder"))

    cases = label_eval_cases.build_cases(activities, [study, other])
    tags = {
        (case["persona"], UUID(case["activity_id"]).int): case["tags"] for case in cases
    }

    assert tags["study", 1] == ["cold_start", "new_host", "personas_disagree"]
    assert tags["study", 2] == ["cold_start", "personas_disagree"]
    assert tags["study", 3] == ["cold_start", "new_host", "personas_disagree"]
    assert tags["study", 4] == ["gold_null"]
    assert tags["study", 5] == ["personas_disagree"]
    assert tags["study", 6] == ["new_host", "personas_disagree"]
    assert tags["study", 7] == ["gold_null"]
    assert tags["other", 4] == ["gold_null"]
    assert tags["other", 6] == ["gold_null", "new_host", "personas_disagree"]


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _inputs(tmp_path: Path, activities: list[dict[str, Any]]) -> list[str]:
    snapshot = tmp_path / "activities.jsonl"
    snapshot.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in activities),
        encoding="utf-8",
    )
    persona = _write(tmp_path / "p.toml", PERSONA.format(name="p", study_app="Code"))
    return ["cases", "--activities", str(snapshot), "--persona", str(persona)]


def test_owner_labels_become_cases_with_labeled_examples_only(
    label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    activities = [
        _activity(number, _minute(number), app_name="Code") for number in range(1, 5)
    ]
    labels = tmp_path / "owner.jsonl"
    labels.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in (
                {"activity_id": str(UUID(int=3)), "label": "쉼"},
                {"activity_id": str(UUID(int=1)), "label": "null"},
            )
        ),
        encoding="utf-8",
    )
    names = _write(tmp_path / "owner.toml", 'labels = ["일", "쉼"]\n')
    out = tmp_path / "cases.jsonl"

    exit_code = label_eval_cases.main(
        [
            *_inputs(tmp_path, activities),
            "--owner-labels",
            str(labels),
            "--owner-label-names",
            str(names),
            "--out",
            str(out),
        ]
    )

    assert exit_code == 0
    cases = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    owner = [case for case in cases if case["persona"] == "owner"]
    owner_id = label_eval_cases.parse_persona(
        {"labels": ["일", "쉼"]}, with_rules=False
    ).label_id
    assert [case["case_id"] for case in owner] == [
        f"owner/{UUID(int=1)}",
        f"owner/{UUID(int=3)}",
    ]
    assert owner[0]["gold"] is None
    assert owner[0]["tags"] == ["cold_start", "gold_null"]
    assert owner[1]["gold"] == owner_id("쉼")
    assert owner[1]["examples"] == [
        {
            "activity_id": str(UUID(int=1)),
            "activity": "app_name: Code",
            "label_id": None,
        }
    ]
    assert owner[1]["labels"] == [
        {"label_id": owner_id("일"), "name": "일"},
        {"label_id": owner_id("쉼"), "name": "쉼"},
    ]
    assert all("personas_disagree" not in case["tags"] for case in cases)
    assert len(cases) == 6


@pytest.mark.parametrize(
    "row",
    [
        {"activity_id": str(UUID(int=1)), "label": "업무"},
        {"activity_id": str(UUID(int=9)), "label": "일"},
        {"activity_id": str(UUID(int=1)), "label": "일", "note": "x"},
    ],
)
def test_invalid_owner_labels_are_rejected(
    label_eval_cases: ModuleType, tmp_path: Path, row: dict[str, str]
) -> None:
    labels = _write(tmp_path / "owner.jsonl", json.dumps(row, ensure_ascii=False))
    names = _write(tmp_path / "owner.toml", 'labels = ["일", "쉼"]\n')
    out = tmp_path / "cases.jsonl"

    exit_code = label_eval_cases.main(
        [
            *_inputs(tmp_path, [_activity(1, _minute(1), app_name="Code")]),
            "--owner-labels",
            str(labels),
            "--owner-label-names",
            str(names),
            "--out",
            str(out),
        ]
    )

    assert exit_code == 2
    assert not out.exists()


def test_same_input_gives_same_bytes_and_a_matching_digest(
    label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    activities = [
        _activity(number, _minute(number % 7), app_name="Code", web_host="한글.example")
        for number in range(1, 30)
    ]
    shuffled = list(activities)
    random.Random(0).shuffle(shuffled)
    outputs = []
    for index, rows in enumerate((activities, shuffled)):
        directory = tmp_path / str(index)
        directory.mkdir()
        out = directory / "cases.jsonl"
        assert (
            label_eval_cases.main([*_inputs(directory, rows), "--out", str(out)]) == 0
        )
        outputs.append(out)

    first, second = (out.read_bytes() for out in outputs)
    assert first == second
    digest = (outputs[0].parent / "cases.jsonl.sha256").read_text(encoding="utf-8")
    assert digest == f"{hashlib.sha256(first).hexdigest()}  cases.jsonl\n"
    line = first.splitlines()[0].decode("utf-8")
    assert "한글.example" in line
    assert line == json.dumps(json.loads(line), ensure_ascii=False, sort_keys=True)


@pytest.mark.parametrize(
    "change",
    [
        {"fields": None},
        {"ended_at": "2026-01-01T00:00:00"},
        {"ended_at": "어제"},
        {"extra": 1},
    ],
)
def test_malformed_snapshot_rows_are_rejected(
    label_eval_cases: ModuleType, tmp_path: Path, change: dict[str, Any]
) -> None:
    out = tmp_path / "cases.jsonl"
    row = {**_activity(1, _minute(1), app_name="Code"), **change}

    exit_code = label_eval_cases.main([*_inputs(tmp_path, [row]), "--out", str(out)])

    assert exit_code == 2
    assert not out.exists()


def test_outputs_are_readable_by_the_owner_only(
    label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    out = tmp_path / "cases.jsonl"
    out.write_text("old", encoding="utf-8")
    out.chmod(0o644)
    activities = [_activity(1, _minute(1), app_name="Code")]

    assert (
        label_eval_cases.main([*_inputs(tmp_path, activities), "--out", str(out)]) == 0
    )

    assert out.stat().st_mode & 0o777 == 0o600
    assert Path(f"{out}.sha256").stat().st_mode & 0o777 == 0o600


def test_unwritable_outputs_are_rejected_without_a_partial_result(
    label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    out = tmp_path / "cases.jsonl"
    Path(f"{out}.sha256").mkdir()
    activities = [_activity(1, _minute(1), app_name="Code")]
    inputs = _inputs(tmp_path, activities)

    assert label_eval_cases.main([*inputs, "--out", str(out)]) == 2
    assert label_eval_cases.main([*inputs, "--out", str(tmp_path)]) == 2

    assert not out.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "activities.jsonl",
        "cases.jsonl.sha256",
        "p.toml",
    ]


def test_symlinked_outputs_are_replaced_not_followed(
    label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    target = tmp_path / "tracked.txt"
    target.write_text("keep", encoding="utf-8")
    out = tmp_path / "cases.jsonl"
    out.symlink_to(target)
    Path(f"{out}.sha256").symlink_to(target)
    activities = [_activity(1, _minute(1), app_name="Code")]

    assert (
        label_eval_cases.main([*_inputs(tmp_path, activities), "--out", str(out)]) == 0
    )

    assert target.read_text(encoding="utf-8") == "keep"
    assert not out.is_symlink()
    assert not Path(f"{out}.sha256").is_symlink()


def test_failed_writes_leave_no_temporary_file(
    label_eval_cases: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "cases.jsonl"
    inputs = _inputs(tmp_path, [_activity(1, _minute(1), app_name="Code")])
    expected = sorted(p.name for p in tmp_path.iterdir())
    create = label_eval_cases._private_temporary
    calls: list[int] = []

    def fail_second(path: Path, content: bytes) -> Path:
        calls.append(1)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return create(path, content)

    class FullDisk:
        def __init__(self, descriptor: int) -> None:
            os.close(descriptor)

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def write(self, _: bytes) -> int:
            raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(label_eval_cases, "_private_temporary", fail_second)
        assert label_eval_cases.main([*inputs, "--out", str(out)]) == 2
    with monkeypatch.context() as patch:
        patch.setattr(label_eval_cases.os, "fdopen", lambda d, _: FullDisk(d))
        assert label_eval_cases.main([*inputs, "--out", str(out)]) == 2

    assert sorted(p.name for p in tmp_path.iterdir()) == expected


def test_symlink_in_a_git_work_tree_to_outside_is_refused(
    label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    out = checkout / "cases.jsonl"
    out.symlink_to(outside / "cases.jsonl")

    with pytest.raises(SystemExit) as exc:
        label_eval_cases.main(
            [
                *_inputs(tmp_path, [_activity(1, _minute(1), app_name="Code")]),
                "--out",
                str(out),
            ]
        )

    assert exc.value.code == 2
    assert out.is_symlink()
    assert list(outside.iterdir()) == []


def test_current_directory_as_output_is_checked_where_it_writes(
    label_eval_cases: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    inputs = _inputs(tmp_path, [_activity(1, _minute(1), app_name="Code")])
    monkeypatch.chdir(checkout)

    for value in (".", ""):
        with pytest.raises(SystemExit) as exc:
            label_eval_cases.main([*inputs, "--out", value])
        assert exc.value.code == 2

    assert sorted(p.name for p in checkout.iterdir()) == [".git"]


def test_output_in_another_git_work_tree_is_refused(
    label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "nested").mkdir(parents=True)
    (checkout / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    out = checkout / "nested" / "cases.jsonl"

    with pytest.raises(SystemExit) as exc:
        label_eval_cases.main(
            [
                *_inputs(tmp_path, [_activity(1, _minute(1), app_name="Code")]),
                "--out",
                str(out),
            ]
        )

    assert exc.value.code == 2
    assert not out.exists()


@pytest.mark.parametrize("option", ["--from", "--to", "--as-of"])
def test_snapshot_times_without_timezone_are_refused(
    label_eval_cases: ModuleType, tmp_path: Path, option: str
) -> None:
    times = {
        "--from": "2026-01-01T00:00:00Z",
        "--to": "2026-01-02T00:00:00Z",
        "--as-of": "2026-01-02T00:00:00Z",
        option: "2026-01-01T00:00:00",
    }
    out = tmp_path / "activities.jsonl"

    with pytest.raises(SystemExit) as exc:
        label_eval_cases.main(
            [
                *("snapshot", "--account-id", str(UUID(int=1))),
                *(item for pair in times.items() for item in pair),
                *("--out", str(out)),
            ]
        )

    assert exc.value.code == 2
    assert not out.exists()


def test_output_inside_the_repository_is_refused(
    label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    repository_file = Path(__file__).resolve().parents[3] / "cases.jsonl"

    with pytest.raises(SystemExit) as exc:
        label_eval_cases.main(
            [
                *_inputs(tmp_path, [_activity(1, _minute(1), app_name="Code")]),
                "--out",
                str(repository_file),
            ]
        )

    assert exc.value.code == 2
    assert not repository_file.exists()


@pytest.mark.parametrize("key", ["window_title", "tab_title"])
@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
def test_line_separators_inside_titles_are_data(
    label_eval_cases: ModuleType, tmp_path: Path, key: str, separator: str
) -> None:
    title = f"a{separator}b"
    out = tmp_path / "cases.jsonl"
    row = _activity(1, _minute(1), app_name="Code", **{key: title})

    exit_code = label_eval_cases.main([*_inputs(tmp_path, [row]), "--out", str(out)])

    assert exit_code == 0
    [case] = [
        json.loads(line) for line in out.read_text(encoding="utf-8").split("\n")[:-1]
    ]
    assert case["fields"][key] == title
    assert f"{key}: {title}" in case["activity"]


def test_crlf_snapshots_with_blank_lines_are_read(
    label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    rows = [_activity(number, _minute(number), app_name="Code") for number in (1, 2)]
    argv = _inputs(tmp_path, rows)
    snapshot = Path(argv[2])
    text = "".join(json.dumps(row, ensure_ascii=False) + "\r\n\r\n" for row in rows)
    snapshot.write_text(text, encoding="utf-8", newline="")
    out = tmp_path / "cases.jsonl"

    assert label_eval_cases.main([*argv, "--out", str(out)]) == 0
    assert len(out.read_text(encoding="utf-8").splitlines()) == len(rows)


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("1" * 5000, id="long-integer"),
        pytest.param("[" * 100_000 + "]" * 100_000, id="deep-array"),
        pytest.param("\u2028", id="line-separator-only"),
    ],
)
def test_unreadable_snapshot_lines_exit_2_and_write_nothing(
    label_eval_cases: ModuleType,
    tmp_path: Path,
    line: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = _inputs(tmp_path, [_activity(1, _minute(1), app_name="Code")])
    snapshot = Path(argv[2])
    snapshot.write_text(
        snapshot.read_text(encoding="utf-8") + line + "\n", encoding="utf-8"
    )
    out = tmp_path / "cases.jsonl"

    assert label_eval_cases.main([*argv, "--out", str(out)]) == 2
    assert not out.exists()
    assert capsys.readouterr().err.startswith("rejected: ")


def test_deeply_nested_persona_exits_2_and_writes_nothing(
    label_eval_cases: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = _inputs(tmp_path, [_activity(1, _minute(1), app_name="Code")])
    persona = Path(argv[4])
    nested = "x = " + "[" * 2000 + "]" * 2000 + "\n"
    persona.write_text(nested + persona.read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / "cases.jsonl"

    assert label_eval_cases.main([*argv, "--out", str(out)]) == 2
    assert not out.exists()
    assert capsys.readouterr().err.startswith("rejected: ")


BIG_DECIMAL = "x = 1" + "0" * 5000 + "\n"
BIG_HEX_LABEL = "0x" + "F" * 4000


@pytest.mark.parametrize(
    "persona_text",
    [
        pytest.param(BIG_DECIMAL + PERSONA, id="big-decimal-value"),
        pytest.param(
            PERSONA.replace('label = "null"', f"label = {BIG_HEX_LABEL}"),
            id="big-hex-rule-label",
        ),
    ],
)
def test_persona_with_oversized_integer_exits_2_and_writes_nothing(
    label_eval_cases: ModuleType,
    tmp_path: Path,
    persona_text: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = _inputs(tmp_path, [_activity(1, _minute(1), app_name="Code")])
    Path(argv[4]).write_text(
        persona_text.format(name="study", study_app="Code"), encoding="utf-8"
    )
    out = tmp_path / "cases.jsonl"

    assert label_eval_cases.main([*argv, "--out", str(out)]) == 2
    assert not out.exists()
    assert capsys.readouterr().err.startswith("rejected: ")


def test_owner_label_names_with_oversized_integer_exit_2_and_write_nothing(
    label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    argv = _inputs(tmp_path, [_activity(1, _minute(1), app_name="Code")])
    owner_labels = tmp_path / "owner-labels.jsonl"
    owner_labels.write_text("", encoding="utf-8")
    names = tmp_path / "owner-names.toml"
    names.write_text(BIG_DECIMAL + 'labels = ["공부", "휴식"]\n', encoding="utf-8")
    out = tmp_path / "cases.jsonl"

    argv += ["--owner-labels", str(owner_labels), "--owner-label-names", str(names)]
    assert label_eval_cases.main([*argv, "--out", str(out)]) == 2
    assert not out.exists()
