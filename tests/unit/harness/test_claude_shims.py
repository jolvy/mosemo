"""`AGENTS.md`마다 같은 폴더에 Claude Code용 `@AGENTS.md` shim(`CLAUDE.md`)을 둔다."""

import os
from pathlib import Path

import pytest

SHIM = b"@AGENTS.md\n"
SKIPPED_FOLDERS = {".git", ".venv", "venv", "node_modules", "__pycache__"}


def skipped(name: str) -> bool:
    return name in SKIPPED_FOLDERS or name.startswith(".")


def instruction_folders(root: Path) -> list[tuple[Path, set[str]]]:
    """건너뛸 폴더를 잘라 내며 순회해 폴더마다 `AGENTS.md`·`CLAUDE.md` 이름을 모은다."""
    folders = []
    for current, names, files in os.walk(root):
        names[:] = sorted(name for name in names if not skipped(name))
        found = {"AGENTS.md", "CLAUDE.md"} & set(files)
        if found:
            folders.append((Path(current), found))
    return folders


def shim_errors(root: Path) -> list[str]:
    errors = []
    for folder, found in instruction_folders(root):
        shim = folder / "CLAUDE.md"
        relative = shim.relative_to(root)
        if "AGENTS.md" not in found:
            errors.append(f"{relative}: 같은 폴더에 AGENTS.md가 없다")
        elif "CLAUDE.md" not in found:
            errors.append(f"{relative}: AGENTS.md 옆에 @AGENTS.md shim이 없다")
        elif shim.read_bytes() != SHIM:
            errors.append(f"{relative}: 내용은 정확히 '@AGENTS.md' 한 줄이어야 한다")
    return errors


def write(root: Path, relative: str, content: bytes = SHIM) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_repository_pairs_every_agents_md_with_shim(repository: Path) -> None:
    assert shim_errors(repository) == []


@pytest.mark.parametrize(
    ("files", "relative"),
    [
        ({"src/AGENTS.md": b"# A\n"}, "src/CLAUDE.md"),
        ({"AGENTS.md": b"# A\n", "CLAUDE.md": b"@AGENTS.md"}, "CLAUDE.md"),
        ({"AGENTS.md": b"# A\n", "CLAUDE.md": b"# Copied\n"}, "CLAUDE.md"),
        ({"AGENTS.md": b"# A\n", "CLAUDE.md": b"@AGENTS.md\n\n"}, "CLAUDE.md"),
        ({"docs/CLAUDE.md": SHIM}, "docs/CLAUDE.md"),
    ],
    ids=[
        "missing-shim",
        "no-trailing-newline",
        "other-content",
        "extra-line",
        "orphan",
    ],
)
def test_shim_violation_is_rejected(
    tmp_path: Path, files: dict[str, bytes], relative: str
) -> None:
    for name, content in files.items():
        write(tmp_path, name, content)

    errors = shim_errors(tmp_path)

    assert errors
    assert all(error.startswith(f"{relative}: ") for error in errors)


def test_paired_shims_and_skipped_folders_pass(tmp_path: Path) -> None:
    for relative in ["AGENTS.md", "tests/AGENTS.md"]:
        write(tmp_path, relative, b"# A\n")
    for relative in ["CLAUDE.md", "tests/CLAUDE.md"]:
        write(tmp_path, relative)
    for relative in [
        ".venv/x/AGENTS.md",
        "venv/x/AGENTS.md",
        "node_modules/x/AGENTS.md",
        "src/__pycache__/AGENTS.md",
        ".git/x/CLAUDE.md",
        ".claude/CLAUDE.md",
        ".codex/x/AGENTS.md",
    ]:
        write(tmp_path, relative, b"# Other\n")

    assert shim_errors(tmp_path) == []
