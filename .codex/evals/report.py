"""평가 결과 집계기.

`uv run --locked python .codex/evals/report.py [결과.jsonl ...]`

`harness_rev`·`harness_dirty`·`model`·`claude --version`·작업별로 실행 간 최소·중앙값·최대를 Markdown 표로
출력한다. 파일을 주지 않으면 `results/`의 모든 JSONL을 읽는다. 합산 점수는 만들지 않는다.
"""

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).resolve().parent / "results"
HEADER = (
    "| harness_rev | dirty | model | claude | 작업 | 실행 | status | 판정 오류 | 판정 통과 | validate 통과 "
    "| 첫 Stop 규칙별 위반 | session_blocks | 비용 USD | 턴(num_turns) | assistant 메시지 "
    "| 도구 호출 | Skill |"
)
GROUP_KEYS = ("harness_rev", "harness_dirty", "model", "claude_version", "task")


def read_rows(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def spread(values: Sequence[float | None]) -> str:
    """값이 없는 실행은 빼고 `최소 / 중앙값 / 최대`. 남는 값이 없으면 `-`."""
    present = sorted(value for value in values if value is not None)
    if not present:
        return "-"
    median = statistics.median(present)
    return " / ".join(f"{value:g}" for value in (present[0], median, present[-1]))


def flag(value: Any) -> float | None:
    return float(value) if isinstance(value, bool) else None


def first_stop_rules(row: dict[str, Any]) -> dict[str, int] | None:
    metrics = row.get("metrics") or []
    if not metrics:
        return None
    rules = metrics[0].get("rules")
    return rules if isinstance(rules, dict) else {}


def session_blocks(row: dict[str, Any]) -> float | None:
    metrics = row.get("metrics") or []
    value = metrics[-1].get("session_blocks") if metrics else None
    return float(value) if isinstance(value, int) else None


def rules_cell(rows: Sequence[dict[str, Any]]) -> str:
    """규칙마다 실행 간 위반 수. 첫 Stop 기록이 있는 실행에서 없는 규칙은 0으로 센다."""
    firsts = [rules for row in rows if (rules := first_stop_rules(row)) is not None]
    if not firsts:
        return "-"
    names = sorted({name for rules in firsts for name in rules})
    if not names:
        return "0"
    return "<br>".join(
        f"{name} {spread([rules.get(name, 0) for rules in firsts])}" for name in names
    )


def skills_cell(rows: Sequence[dict[str, Any]]) -> str:
    """Skill마다 그 Skill을 쓴 실행 수 / 전체 실행 수."""
    counts: Counter[str] = Counter()
    for row in rows:
        used = {
            skill
            for entry in row.get("metrics") or []
            for skill in entry.get("skills", [])
        }
        counts.update(used)
    if not counts:
        return "-"
    return ", ".join(
        f"{name} {count}/{len(rows)}" for name, count in sorted(counts.items())
    )


def judgement_error_cell(rows: Sequence[dict[str, Any]]) -> str:
    """판정 단계 오류가 난 실행 수 / 전체. 그 실행은 판정 통과 칸에서 빠진다."""
    failed = sum(bool(row.get("judgement_error")) for row in rows)
    return f"{failed}/{len(rows)}"


def status_cell(rows: Sequence[dict[str, Any]]) -> str:
    counts = Counter(str(row.get("status")) for row in rows)
    return ", ".join(f"{status} {count}" for status, count in sorted(counts.items()))


def group_rows(
    rows: Iterable[dict[str, Any]],
) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    groups: defaultdict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row.get(key)) for key in GROUP_KEYS)].append(row)
    return dict(sorted(groups.items()))


def report(rows: Iterable[dict[str, Any]]) -> str:
    lines = [HEADER, "| " + " | ".join(["---"] * (HEADER.count("|") - 1)) + " |"]
    for key, group in group_rows(rows).items():
        cells = [
            *key,
            str(len(group)),
            status_cell(group),
            judgement_error_cell(group),
            spread([flag(row.get("acceptance")) for row in group]),
            spread([flag(row.get("validate")) for row in group]),
            rules_cell(group),
            spread([session_blocks(row) for row in group]),
            spread([row.get("cost_usd") for row in group]),
            spread([row.get("turns") for row in group]),
            spread([row.get("assistant_messages") for row in group]),
            spread([row.get("tool_uses") for row in group]),
            skills_cell(group),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path)
    arguments = parser.parse_args(argv)
    files = arguments.files or sorted(RESULTS.glob("*.jsonl"))
    sys.stdout.write(report(read_rows(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
