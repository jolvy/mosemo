from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest
from coverage import CoverageData

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/check_coverage.py"


@pytest.fixture(scope="module")
def check_coverage() -> ModuleType:
    spec = spec_from_file_location("check_coverage", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("lines", "branches", "expected"),
    [
        ((95, 100), (90, 100), (True, True)),
        ((1899, 2000), (90, 100), (False, True)),
        ((95, 100), (524, 582), (True, True)),
        ((95, 100), (523, 582), (True, False)),
        ((95, 100), (179, 200), (True, False)),
        ((94, 100), (89, 100), (False, False)),
    ],
)
def test_evaluate_compares_exact_ratios_without_rounding(
    check_coverage: ModuleType,
    lines: tuple[int, int],
    branches: tuple[int, int],
    expected: tuple[bool, bool],
) -> None:
    line, branch = check_coverage.evaluate(lines=lines, branches=branches)

    assert (line.threshold, branch.threshold) == (95, 90)
    assert (line.passed, branch.passed) == expected


def test_main_reports_every_measurement_and_fails_when_any_is_short(
    check_coverage: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        check_coverage,
        "load_totals",
        lambda: {
            "covered_lines": 94,
            "num_statements": 100,
            "covered_branches": 179,
            "num_branches": 200,
        },
    )

    assert check_coverage.main() == 1
    assert capsys.readouterr().out.splitlines() == [
        "line coverage: 94/100 (94.00%), required 95%: FAIL",
        "branch coverage: 179/200 (89.50%), required 90%: FAIL",
    ]


def test_main_passes_when_both_measurements_meet_thresholds(
    check_coverage: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        check_coverage,
        "load_totals",
        lambda: {
            "covered_lines": 95,
            "num_statements": 100,
            "covered_branches": 524,
            "num_branches": 582,
        },
    )

    assert check_coverage.main() == 0


def test_load_totals_fails_without_coverage_data(
    check_coverage: ModuleType, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit, match="no coverage data"):
        check_coverage.load_totals(str(tmp_path / ".coverage"))


def test_load_totals_fails_without_branch_data(
    check_coverage: ModuleType, tmp_path: Path
) -> None:
    data_file = tmp_path / ".coverage"
    data = CoverageData(basename=str(data_file))
    data.add_lines({str(SCRIPT): [1]})
    data.write()

    with pytest.raises(SystemExit, match="no branch data"):
        check_coverage.load_totals(str(data_file))
