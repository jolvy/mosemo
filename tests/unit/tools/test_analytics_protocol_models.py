"""일간 통계 프로토콜 상태 모델(`tools/analytics_protocol_models`)의 작은 시나리오.

pytest의 import 경로에는 `src`만 있으므로 모델을 `python -m`으로 저장소 루트에서 실행하고, 종료 코드와
마지막 요약으로 판정한다. 모델은 변형별 결과가 자기 기대 결과 상수와 다르면 종료 코드 1로 끝난다.
"""

import re
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
PACKAGE = "tools.analytics_protocol_models"
# 작은 시나리오는 모델마다 1초 안팎이다. 탐색이 멈추거나 커져도 테스트가 끝나도록 막는다.
MODEL_TIMEOUT_SECONDS = 60


def run_model(module: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", f"{PACKAGE}.{module}", *args],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        timeout=MODEL_TIMEOUT_SECONDS,
        check=False,
    )


def summary(stdout: str) -> dict[str, frozenset[str]]:
    """마지막 요약의 변형별 위반 불변식 집합(통과 = 빈 집합)."""
    lines = stdout.split("=== 요약\n", 1)[1].split("\n\n", 1)[0].splitlines()
    result = {}
    for line in lines:
        name, verdict, *rest = line.split()
        result[name] = (
            frozenset() if verdict == "통과" else frozenset(rest[0].split(","))
        )
    return result


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        (
            "protocol_model",
            {"V5": frozenset(), "V3": frozenset({"I1", "I2"})},
        ),
        (
            "protocol_model_4b",
            {
                "F5": frozenset(),
                "N5": frozenset({"I4"}),
                # 요약은 모델이 센 I6g를 그대로 보이고, 기대 결과 비교는 I6과 함께 나온 I6g를 I6에 포함된 것으로 본다.
                "N2": frozenset({"I1", "I2", "I6", "I6g"}),
            },
        ),
        ("protocol_model_tz", {"Z1": frozenset(), "Z0": frozenset({"I8"})}),
        ("protocol_model_tz3", {"Ax": frozenset(), "As": frozenset({"I8"})}),
        (
            "protocol_model_archive",
            {"R3": frozenset(), "X1": frozenset({"I1", "I12"})},
        ),
    ],
)
def test_small_scenario_matches_expected_results(
    module: str, expected: dict[str, frozenset[str]]
) -> None:
    completed = run_model(module, "--small")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert summary(completed.stdout) == expected


def test_tz3_distinct_counts_small_scenario() -> None:
    completed = run_model("protocol_model_tz3_distinct", "--small")

    assert completed.returncode == 0, completed.stderr
    # 옮기기 전 원본의 실행 출력(protocol_model_tz3_distinct_run.txt) 첫 줄과 같은 값: L3A·변경 없음에서
    # 시간대 변경 커밋 직전 상태 36개 모두 (a)의 대상이 d3을 빼고, d3에는 새 tz 활동이 없다.
    first = completed.stdout.splitlines()[0]
    assert re.fullmatch(
        r"L3A·초기 stored·변경 없음 +커밋 직전 상태 +36 +\(a\)≠모든 날짜 +36 +"
        r"\{\(\('d3',\), '빠진 날짜는 새 tz 활동 없음'\): 36\}",
        first,
    ), first


def test_session_marks_recommended_passes_and_contrasts_violate() -> None:
    completed = run_model("session_marks")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = summary(completed.stdout)
    assert result["S0"] == frozenset()
    assert "I1" in result["S1"]
    assert result["S2"] & {"I1", "I2"}
    assert result["S3"] & {"I1", "I2"}


def load_4b(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """4b 모델을 파일 경로로 불러온다(dataclass가 sys.modules의 모듈을 찾으므로 등록한다)."""
    name = "analytics_protocol_model_4b"
    path = REPOSITORY / "tools/analytics_protocol_models/protocol_model_4b.py"
    spec = spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("kinds", "matches_n2"),
    [
        ({"I1", "I2", "I6", "I6g"}, True),
        ({"I1", "I2", "I6"}, True),
        # 격리 표시의 I6이 사라지고 격리되지 않은 I6g만 남은 회귀는 N2 기대 결과와 다르다.
        ({"I1", "I2", "I6g"}, False),
    ],
)
def test_4b_comparison_counts_i6g_only_alongside_i6(
    monkeypatch: pytest.MonkeyPatch, kinds: set[str], matches_n2: bool
) -> None:
    model = load_4b(monkeypatch)

    assert (model.comparable(frozenset(kinds)) == model.EXPECTED["N2"]) is matches_n2


def test_4b_comparison_keeps_i6g_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    model = load_4b(monkeypatch)

    assert model.comparable(frozenset({"I6g"})) == frozenset({"I6g"})
