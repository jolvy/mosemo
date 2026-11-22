import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs" / "label-engine-eval.md"
OPTION = re.compile(r"--[a-z-]+")
COMMANDS = {
    "cases snapshot": ("scripts/label_eval_cases.py", "snapshot"),
    "cases cases": ("scripts/label_eval_cases.py", "cases"),
    "run": ("scripts/label_eval_run.py",),
    "score": ("scripts/label_eval_score.py",),
}


def _help_options(arguments: tuple[str, ...]) -> set[str]:
    completed = subprocess.run(
        [sys.executable, *arguments, "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return set(OPTION.findall(completed.stdout)) - {"--help"}


def _table_options(command: str) -> set[str]:
    section = re.search(
        rf"^### {re.escape(command)}\n(.*?)(?=^#{{2,3}} |\Z)",
        GUIDE.read_text(encoding="utf-8"),
        re.MULTILINE | re.DOTALL,
    )
    assert section is not None, command
    table = re.search(r"^\|.*?(?=\n\n|\Z)", section[1], re.MULTILINE | re.DOTALL)
    assert table is not None, command
    rows = table[0].splitlines()[2:]
    return {row.split("|")[1].strip().strip("`") for row in rows}


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_option_table_matches_help(command: str) -> None:
    assert _table_options(command) == _help_options(COMMANDS[command])
