"""일일 보고서 실행기를 매일 09:00(시스템 현지 시각)에 실행하는 launchd plist를 쓴다.

plist 파일만 쓰고 `launchctl`은 부르지 않는다. 같은 입력이면 같은 바이트를 쓴다.
"""

import argparse
import plistlib
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

LABEL = "dev.jolvy.mosemo-agent-report"
DEFAULT_PLIST = f"~/Library/LaunchAgents/{LABEL}.plist"
DEFAULT_REPORTS = "~/mosemo-reports"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
FAILURE_TITLE = "mosemo 보고서 실패"


def program_command(*, uv: str, repository: Path, reports: Path) -> str:
    """실행기가 0이 아닌 코드로 끝나면 대체 실패 알림을 보내고 원래 코드로 끝나는 셸 명령."""
    run = " ".join(
        [
            shlex.quote(uv),
            "run --locked --directory",
            shlex.quote(str(repository)),
            "python .codex/reports/run_daily.py --reports-dir",
            shlex.quote(str(reports)),
        ]
    )
    body = str(reports / ".logs/launchd.err").replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{body}" with title "{FAILURE_TITLE}"'
    return (
        f"{run}; code=$?; "
        f"if [ $code -ne 0 ]; then /usr/bin/osascript -e {shlex.quote(script)}; fi; "
        "exit $code"
    )


def plist_data(
    *, uv: str, claude: str, repository: Path, reports: Path
) -> dict[str, Any]:
    folders = dict.fromkeys([str(Path(uv).parent), str(Path(claude).parent)])
    return {
        "Label": LABEL,
        "ProgramArguments": [
            "/bin/sh",
            "-c",
            program_command(uv=uv, repository=repository, reports=reports),
        ],
        "StartCalendarInterval": {"Hour": 9, "Minute": 0},
        "EnvironmentVariables": {"PATH": ":".join([*folders, SYSTEM_PATH])},
        "StandardOutPath": str(reports / ".logs/launchd.out"),
        "StandardErrorPath": str(reports / ".logs/launchd.err"),
        "RunAtLoad": False,
    }


def main_checkout() -> Path:
    """이 파일이 있는 저장소 본체(공통 git 디렉터리의 부모). 읽기 전용 git 명령만 쓴다."""
    output = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return Path(output.strip()).parent


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plist", type=Path, default=Path(DEFAULT_PLIST))
    parser.add_argument("--reports-dir", type=Path, default=Path(DEFAULT_REPORTS))
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
    repository: Path | None = None,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    uv, claude = which("uv"), which("claude")
    if uv is None or claude is None:
        missing = "uv" if uv is None else "claude"
        sys.stderr.write(f"{missing}를 PATH에서 찾지 못함\n")
        return 1
    data = plist_data(
        uv=str(Path(uv).absolute()),
        claude=str(Path(claude).absolute()),
        repository=repository or main_checkout(),
        reports=args.reports_dir.expanduser().resolve(),
    )
    plist = args.plist.expanduser()
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps(data, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
