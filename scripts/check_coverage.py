"""직전 `pytest --cov` 데이터의 라인·브랜치 합계를 각각 기준과 비교한다."""

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from coverage import Coverage

LINE_THRESHOLD = 95
BRANCH_THRESHOLD = 90


@dataclass(frozen=True)
class Measurement:
    name: str
    covered: int
    total: int
    threshold: int

    @property
    def passed(self) -> bool:
        # coverage.py의 fail_under처럼 반올림하지 않는다.
        return self.covered * 100 >= self.threshold * self.total

    def describe(self) -> str:
        percent = self.covered / self.total * 100 if self.total else 100.0
        verdict = "pass" if self.passed else "FAIL"
        return (
            f"{self.name} coverage: {self.covered}/{self.total} ({percent:.2f}%), "
            f"required {self.threshold}%: {verdict}"
        )


def evaluate(
    *, lines: tuple[int, int], branches: tuple[int, int]
) -> tuple[Measurement, Measurement]:
    return (
        Measurement("line", *lines, LINE_THRESHOLD),
        Measurement("branch", *branches, BRANCH_THRESHOLD),
    )


def load_totals(data_file: str = ".coverage") -> dict[str, int]:
    coverage = Coverage(data_file=data_file)
    coverage.load()
    data = coverage.get_data()
    if not data.measured_files():
        raise SystemExit(f"no coverage data in {data_file}; run pytest --cov first")
    if not data.has_arcs():
        raise SystemExit(f"{data_file} has no branch data; enable branch coverage")
    with TemporaryDirectory() as directory:
        report = Path(directory) / "coverage.json"
        coverage.json_report(outfile=str(report))
        return json.loads(report.read_text(encoding="utf-8"))["totals"]


def main() -> int:
    totals = load_totals()
    measurements = evaluate(
        lines=(totals["covered_lines"], totals["num_statements"]),
        branches=(totals["covered_branches"], totals["num_branches"]),
    )
    for measurement in measurements:
        print(measurement.describe())
    return 0 if all(measurement.passed for measurement in measurements) else 1


if __name__ == "__main__":
    sys.exit(main())
