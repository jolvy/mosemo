"""Case rows for the label eval runner tests (``cases.jsonl`` contract, #315)."""

import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx2


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


class Decisions:
    """An ``httpx2.MockTransport`` handler answering from a queue; keeps requests."""

    def __init__(self, *responses: httpx2.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))

    def bodies(self) -> list[Any]:
        return [json.loads(request.content) for request in self.requests]


# Response bodies in the documented schemas (Plan #309, Issue #317 links).


def b_choice(
    name: str,
    choice: str,
    *,
    confidence: float = 0.8,
    probabilities: dict[str, float] | None = None,
) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "type": "choice",
        "name": name,
        "choice": choice,
        "confidence": confidence,
    }
    if probabilities is not None:
        answer["probabilities"] = [
            {"value": value, "probability": p} for value, p in probabilities.items()
        ]
    return answer


def b_refusal(name: str) -> dict[str, Any]:
    return {"type": "refusal", "name": name}


def b_response(*answers: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "model": "gpt-6-luna",
            "answers": list(answers),
            "usage": {
                "input_tokens": 42,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens": 3,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 45,
            },
        },
    )


def c_choice(
    choice: str,
    *,
    confidence: float = 0.8,
    probabilities: dict[str, float] | None = None,
) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "type": "choice",
        "choice": choice,
        "confidence": confidence,
    }
    if probabilities is not None:
        answer["probabilities"] = probabilities
    return answer


C_COSTS = {"aimlapi": 2.25e-05, "openrouter": 0.000019992, "ollama": None}


def c_response(route: str, answers: dict[str, dict[str, Any]]) -> httpx2.Response:
    """A route's documented response shape; only the cost field differs."""
    if route == "aimlapi":
        body = {
            "model": "jev-1.14.0",
            "answers": answers,
            "usage": {"input_tokens": 406, "output_tokens": 73},
            "meta": {
                "model": "typesafe/jev-1.14",
                "provider": "typesafe",
                "usage": {"credits_used": 45, "usd_spent": C_COSTS["aimlapi"]},
                "metrics": {"duration_ms": 96},
            },
        }
    elif route == "openrouter":
        body = {
            "answers": answers,
            "id": "gen-dec-1789738314-X5e5eKGQdvR9rblyX250",
            "model": "typesafe/jev-1.13-20260917",
            "provider": "TypeSafe",
            "usage": {
                "cost": C_COSTS["openrouter"],
                "input_tokens": 476,
                "output_tokens": 70,
            },
        }
    else:
        body = {
            "model": "laya",
            "answers": answers,
            "usage": {"input_tokens": 412, "output_tokens": 4},
        }
    return httpx2.Response(200, json=body)


def write_cases(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
