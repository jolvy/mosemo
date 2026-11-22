"""Case rows for the label eval runner tests (``cases.jsonl`` contract, #315)."""

import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5


def label_id(persona: str, label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"mosemo-label-eval/{persona}/{label}"))


def case_row(
    number: int,
    *,
    persona: str = "p",
    gold: str | None = "공부",
    examples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    activity_id = str(uuid5(NAMESPACE_URL, f"activity/{number}"))
    return {
        "case_id": f"{persona}/{activity_id}",
        "persona": persona,
        "activity_id": activity_id,
        "activity": f"app_name: App{number}",
        "fields": {"app_name": f"App{number}"},
        "labels": [
            {"label_id": label_id(persona, name), "name": name}
            for name in ("공부", "휴식")
        ],
        "examples": examples or [],
        "gold": None if gold is None else label_id(persona, gold),
        "tags": [],
    }


def write_cases(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
