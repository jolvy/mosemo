"""Case and result rows for the label eval scoring tests (Plan #309 contracts)."""

import json
from pathlib import Path
from types import ModuleType
from typing import Any

L1 = "label-1"
L2 = "label-2"


def case(case_id: str, gold: str | None, *tags: str) -> dict[str, Any]:
    persona, activity_id = case_id.split("/")
    return {
        "case_id": case_id,
        "persona": persona,
        "activity_id": activity_id,
        "activity": f"app_name: {activity_id}",
        "fields": {},
        "labels": [{"label_id": L1, "name": "공부"}, {"label_id": L2, "name": "휴식"}],
        "examples": [],
        "gold": gold,
        "tags": list(tags),
    }


def result(case_id: str, engine: str, **values: Any) -> dict[str, Any]:
    row = {
        "case_id": case_id,
        "engine": engine,
        "batch_size": 1,
        "rep": 0,
        "pred": None,
        "confidence": None,
        "prob": None,
        "refused": False,
        "latency_ms": 100,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "attempts": 1,
        "error": None,
    } | values
    row.setdefault("call_id", f"{engine}/{row['batch_size']}/{case_id}/{row['rep']}")
    return row


def answers(
    module: ModuleType, cases: list[dict[str, Any]], results: list[dict[str, Any]]
) -> list[Any]:
    by_id = {row["case_id"]: module.parse_case(row) for row in cases}
    parsed = module.latest_results(module.parse_result(row) for row in results)
    return module.join(by_id, parsed)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path
