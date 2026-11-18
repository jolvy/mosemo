"""`sync_openwiki_update.py`가 검증을 통과한 OpenWiki 생성 결과만 `openwiki/`에 반영한다."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY / ".codex/skills/verification/scripts/sync_openwiki_update.py"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def generated(tmp_path: Path) -> Path:
    root = tmp_path / "generated"
    write(root / ".last-update.json", json.dumps({"status": "complete"}))
    write(root / "quickstart.md", "new quickstart\n")
    write(root / ".claims/quickstart.json", "{}\n")
    write(root / "INSTRUCTIONS.md", "model edited instructions\n")
    return root


@pytest.fixture
def target(tmp_path: Path) -> Path:
    root = tmp_path / "openwiki"
    write(root / "INSTRUCTIONS.md", "human instructions\n")
    write(root / "quickstart.md", "old quickstart\n")
    write(root / "removed/page.md", "removed page\n")
    return root


def run(generated: Path, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--generated", str(generated)]
        + ["--target", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )


def files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in root.rglob("*")
        if path.is_file()
    }


def test_replaces_generated_pages_and_keeps_instructions(
    generated: Path, target: Path
) -> None:
    result = run(generated, target)

    assert result.returncode == 0, result.stdout
    assert files(target) == {
        "INSTRUCTIONS.md": "human instructions\n",
        "quickstart.md": "new quickstart\n",
        ".claims/quickstart.json": "{}\n",
        ".last-update.json": json.dumps({"status": "complete"}),
    }


@pytest.mark.parametrize("name", ["conftest.py", ".gitattributes", "hooks/run.sh"])
def test_rejects_disallowed_extension(generated: Path, target: Path, name: str) -> None:
    write(generated / name, "x\n")

    result = run(generated, target)

    assert result.returncode == 1
    assert f"{name}: 허용하지 않는 확장자다" in result.stdout.splitlines()
    assert (target / "quickstart.md").read_text(encoding="utf-8") == "old quickstart\n"


@pytest.mark.parametrize("link", ["link.md", "linked-dir"])
def test_rejects_symlink(generated: Path, target: Path, link: str) -> None:
    (generated / link).symlink_to(target)

    result = run(generated, target)

    assert result.returncode == 1
    assert f"{link}: 일반 파일이 아니다" in result.stdout.splitlines()


# 실제 토큰처럼 보이는 문자열을 소스에 그대로 두지 않도록 본문을 만들어 붙인다.
BODY = "a1B2" * 9


@pytest.mark.parametrize("prefix", ["sk-ant-oat01-", "github_pat_", "ghp_", "ghs_"])
def test_rejects_token_like_text(generated: Path, target: Path, prefix: str) -> None:
    token = prefix + BODY
    write(generated / "leak.md", f"value {token}\n")

    result = run(generated, target)

    assert result.returncode == 1
    assert "leak.md: 토큰 형태의 문자열이 있다" in result.stdout.splitlines()


@pytest.mark.parametrize("status", ["interrupted", None])
def test_rejects_incomplete_run(
    generated: Path, target: Path, status: str | None
) -> None:
    if status is None:
        (generated / ".last-update.json").unlink()
        expected = ".last-update.json: 없다"
    else:
        write(generated / ".last-update.json", json.dumps({"status": status}))
        expected = ".last-update.json: status가 complete가 아니다"

    result = run(generated, target)

    assert result.returncode == 1
    assert expected in result.stdout.splitlines()


def test_allows_token_prefix_mentioned_in_docs(generated: Path, target: Path) -> None:
    # 위키가 이 검사를 설명할 때 접두어만 적는다.
    write(
        generated / "harness.md", "`sk-ant-`·`ghp_`·`github_pat_` 등 토큰 형태 문자열\n"
    )

    assert run(generated, target).returncode == 0
