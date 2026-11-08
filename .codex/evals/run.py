"""`cases.csv` 문제를 Claude Code와 Codex에서 읽기 전용으로 실행하고 Skill 호출을 판정한다.

에이전트별 차이는 명령 만들기와 기록에서 Skill 호출 찾기 두 함수뿐이다. 결과는
`.codex/evals/results/UTC_STAMP/results.jsonl`에 쓰고 끝에 문제·에이전트별 요약표를
출력한다. 모든 `triggered`가 null이 아니고 `control-` 문제가 모든 실행에서 true일 때만
종료 코드 0이다.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# `.codex/hooks/stop_metrics.py`를 불러온다. 경로 변경만 import 사이에 둔다(E402).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import stop_metrics

ROOT = Path(__file__).resolve().parents[2]
EVALS = Path(__file__).resolve().parent
CASES = EVALS / "cases.csv"
RESULTS = EVALS / "results"
CASE_COLUMNS = ["id", "skill", "should_trigger", "prompt"]
CONTROL_PREFIX = "control-"
CASE_RUNS = 3
CONTROL_RUNS = 1
CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_BUDGET_USD = "0.5"
CODEX_MODEL = "gpt-6.1-sol"
TIMEOUT_SECONDS = 600
PARENT_VARIABLES = ("CLAUDECODE", "CLAUDE_PID")
PARENT_PREFIX = "CLAUDE_CODE_"
# Codex 명령 문자열 안 어디에 있어도 찾는다. hook용 `stop_metrics.SKILL_FILE`과 다르다.
COMMAND_SKILL_FILE = re.compile(r"\.(?:codex|claude)/skills/([^/\s'\"]+)/SKILL\.md")


@dataclass(frozen=True)
class Case:
    id: str
    skill: str
    should_trigger: bool
    prompt: str

    @property
    def runs(self) -> int:
        return CONTROL_RUNS if self.id.startswith(CONTROL_PREFIX) else CASE_RUNS


@dataclass(frozen=True)
class Record:
    """한 실행 기록에서 찾은 Skill 이름과 결과 줄에 더할 값. 읽지 못했으면 `error`."""

    skills: list[str]
    extra: dict[str, Any]
    error: str | None = None


@dataclass(frozen=True)
class Agent:
    command: Callable[[], list[str]]
    find: Callable[[Sequence[str]], Record]


@dataclass(frozen=True)
class Outcome:
    exit_code: int | None
    lines: list[str]
    error: str | None = None


def read_cases(text: str) -> list[Case]:
    rows = list(csv.DictReader(text.splitlines()))
    return [
        Case(row["id"], row["skill"], row["should_trigger"] == "true", row["prompt"])
        for row in rows
    ]


def claude_command() -> list[str]:
    return [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        CLAUDE_MODEL,
        "--max-budget-usd",
        CLAUDE_BUDGET_USD,
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        "Read,Grep,Glob,Skill",
        "--strict-mcp-config",
        "--settings",
        '{"disableAllHooks": true}',
        "--no-session-persistence",
    ]


def codex_command() -> list[str]:
    return [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--ignore-user-config",
        "--disable",
        "hooks",
        "-m",
        CODEX_MODEL,
        "-c",
        'model_reasoning_effort="low"',
    ]


def child_env(parent: Mapping[str, str]) -> dict[str, str]:
    """부모 세션 표식만 지운다. 인증 변수와 그 밖의 변수는 그대로 둔다."""
    return {
        name: value
        for name, value in parent.items()
        if name not in PARENT_VARIABLES and not name.startswith(PARENT_PREFIX)
    }


def json_objects(lines: Iterable[str]) -> list[dict[str, Any]]:
    objects = []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects


def claude_record(lines: Sequence[str]) -> Record:
    events = json_objects(lines)
    if not events:
        return Record([], {}, "no readable stream-json line")
    skills, model = stop_metrics.transcript_facts(lines)
    results = [event for event in events if event.get("type") == "result"]
    result = results[-1] if results else {}
    extra = {"model": model, "cost_usd": result.get("total_cost_usd")}
    if result.get("is_error"):
        return Record(skills, extra, f"result {result.get('subtype')}")
    return Record(skills, extra)


def codex_record(lines: Sequence[str]) -> Record:
    events = json_objects(lines)
    if not events:
        return Record([], {"model": CODEX_MODEL}, "no readable json line")
    skills: dict[str, None] = {}
    tokens = None
    for event in events:
        item = event.get("item")
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "command_execution"
        ):
            command = str(item.get("command", ""))
            skills.update(dict.fromkeys(COMMAND_SKILL_FILE.findall(command)))
        if event.get("type") == "turn.completed":
            tokens = event.get("usage")
    return Record(list(skills), {"model": CODEX_MODEL, "tokens": tokens})


AGENTS = {
    "claude": Agent(claude_command, claude_record),
    "codex": Agent(codex_command, codex_record),
}


def judge(case: Case, outcome: Outcome, *, agent: str, run: int) -> dict[str, Any]:
    record = AGENTS[agent].find(outcome.lines)
    error = outcome.error or record.error
    if error is None and outcome.exit_code != 0:
        error = f"exit code {outcome.exit_code}"
    return {
        "case": case.id,
        "agent": agent,
        "run": run,
        "triggered": None if error else case.skill in record.skills,
        "expected": case.should_trigger,
        "exit_code": outcome.exit_code,
        "error": error,
        "model": None,
        "cost_usd": None,
        "tokens": None,
    } | record.extra


def summary(rows: Sequence[Mapping[str, Any]]) -> str:
    """문제·에이전트별 `triggered` true·false·null 수와 기대 값을 표로 만든다."""
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["case"], row["agent"]), []).append(row)
    lines = [
        "| case | agent | expected | true | false | null |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for (case, agent), group in groups.items():
        values = [row["triggered"] for row in group]
        lines.append(
            f"| {case} | {agent} | {str(group[0]['expected']).lower()} "
            f"| {values.count(True)} | {values.count(False)} "
            f"| {sum(value is None for value in values)} |"
        )
    return "\n".join(lines)


def exit_status(rows: Sequence[Mapping[str, Any]]) -> int:
    for row in rows:
        if row["triggered"] is None:
            return 1
        if row["case"].startswith(CONTROL_PREFIX) and row["triggered"] is not True:
            return 1
    return 0


def execute(command: list[str], prompt: str) -> Outcome:
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=child_env(os.environ),
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return Outcome(None, [], f"{type(error).__name__}: {error}")
    return Outcome(completed.returncode, completed.stdout.splitlines())


def main(argv: Sequence[str] | None = None) -> int:
    cases = read_cases(CASES.read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[case.id for case in cases])
    arguments = parser.parse_args(argv)
    selected = [case for case in cases if arguments.case in {None, case.id}]
    output = RESULTS / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    with (output / "results.jsonl").open("w", encoding="utf-8") as file:
        for case in selected:
            for name, agent in AGENTS.items():
                for run in range(1, case.runs + 1):
                    outcome = execute(agent.command(), case.prompt)
                    row = judge(case, outcome, agent=name, run=run)
                    rows.append(row)
                    file.write(json.dumps(row, ensure_ascii=False) + "\n")
                    file.flush()
                    print(json.dumps(row, ensure_ascii=False), flush=True)
    print(summary(rows))
    print(f"results: {output / 'results.jsonl'}")
    return exit_status(rows)


if __name__ == "__main__":
    sys.exit(main())
