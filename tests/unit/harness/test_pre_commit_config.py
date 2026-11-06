"""pre-commit의 테스트 hook은 `.env.example`을 읽는 `poe test`로 pytest를 실행한다."""

from pathlib import Path

import pytest
import yaml

POE_TEST_HOOKS = ("harness-tests", "pytest-push")
POE_TEST_PREFIX = "uv run --locked poe test "


def hooks(config: Path) -> list[dict[str, object]]:
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    return [hook for repo in data["repos"] for hook in repo["hooks"]]


def direct_pytest_errors(config: Path) -> list[str]:
    """(a) 어떤 hook의 `entry`에도 `pytest`가 없어야 한다."""
    return [
        f"{hook['id']}: entry가 pytest를 직접 실행한다"
        for hook in hooks(config)
        if "pytest" in str(hook.get("entry", ""))
    ]


def poe_test_errors(config: Path) -> list[str]:
    """(b) 테스트 hook이 모두 있고 `uv run --locked poe test `로 시작해야 한다."""
    entries = {hook["id"]: str(hook.get("entry", "")) for hook in hooks(config)}
    errors = []
    for hook_id in POE_TEST_HOOKS:
        if hook_id not in entries:
            errors.append(f"{hook_id}: hook이 없다")
        elif not entries[hook_id].startswith(POE_TEST_PREFIX):
            errors.append(f"{hook_id}: entry가 '{POE_TEST_PREFIX}'로 시작하지 않는다")
    return errors


def write_config(root: Path, entries: dict[str, str]) -> Path:
    config = root / ".pre-commit-config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "repos": [
                    {
                        "repo": "local",
                        "hooks": [
                            {"id": hook_id, "entry": entry}
                            for hook_id, entry in entries.items()
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return config


GOOD_ENTRIES = {
    "harness-tests": "uv run --locked poe test -q tests/unit/harness",
    "pytest-push": "uv run --locked poe test -q tests",
    "lint-imports": "uv run --locked poe lint-imports",
}


def test_repository_test_hooks_use_poe_test(repository: Path) -> None:
    config = repository / ".pre-commit-config.yaml"

    assert direct_pytest_errors(config) == []
    assert poe_test_errors(config) == []


def test_poe_test_hooks_pass(tmp_path: Path) -> None:
    config = write_config(tmp_path, GOOD_ENTRIES)

    assert direct_pytest_errors(config) == []
    assert poe_test_errors(config) == []


@pytest.mark.parametrize(
    "entry",
    [
        "uv run --locked --env-file .env pytest -q tests",
        "uv run --locked python -m pytest -q tests/unit/harness",
    ],
    ids=["env-file-pytest", "python-m-pytest"],
)
def test_direct_pytest_entry_is_rejected(tmp_path: Path, entry: str) -> None:
    config = write_config(tmp_path, {**GOOD_ENTRIES, "pytest-push": entry})

    assert direct_pytest_errors(config) == [
        "pytest-push: entry가 pytest를 직접 실행한다"
    ]


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (
            {"pytest-push": GOOD_ENTRIES["pytest-push"]},
            ["harness-tests: hook이 없다"],
        ),
        (
            {**GOOD_ENTRIES, "harness-tests": "uv run --locked poe lint-imports"},
            ["harness-tests: entry가 'uv run --locked poe test '로 시작하지 않는다"],
        ),
    ],
    ids=["missing-hook", "other-entry"],
)
def test_test_hook_without_poe_test_is_rejected(
    tmp_path: Path, entries: dict[str, str], expected: list[str]
) -> None:
    config = write_config(tmp_path, entries)

    assert poe_test_errors(config) == expected
