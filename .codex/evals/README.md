# Claude Code 평가 세트

하네스(Skill·`AGENTS.md`·rubric·검사기·hook) 변경의 효과를 같은 작업으로 비교한다. 작업 코드는 base
commit에 고정하고 하네스 파일만 평가 대상 revision으로 바꿔 끼운다. 계획의 원본은 Plan #186
「평가 세트(T1, T7)」와 #193이다.

## 구성

- `tasks/E1.md`~`E3.md`: 지시문(`## 지시문` 아래), 고정 base commit(`- base:`), 실행당 비용 상한
  (`- budget_usd:`). base commit은 바꾸지 않는다.
- `acceptance/`: 판정 파일. 작업 공간에는 판정 명령 직전에만 복사한다.
  - `E1.py`·`E2.py`: HTTP 블랙박스 Integration 테스트. `tests/integration/labels/test_eval_acceptance.py`로
    복사해 실행한다. base commit에 있던 심볼과 fixture만 쓴다.
  - `E3.py`: `FocusSession.validate_activity`를 항상 통과하는 구현으로 바꾸는 pytest 플러그인.
- `run.py`: 실행기. `report.py`: 집계기. 순수 함수 테스트는 `tests/unit/harness/test_evals.py`.
- `results/`: 결과 JSONL(`YYYYMMDD-<harness_rev>.jsonl`). 커밋하지 않는다(`.gitignore`).

## 실행

Docker가 동작 중이어야 한다(`validate.sh`의 `poe coverage`, E1·E2 판정).

```sh
uv run --locked python .codex/evals/run.py --task E3 --runs 1            # 작업 트리의 하네스
uv run --locked python .codex/evals/run.py --task E1 --runs 3 --harness REV
uv run --locked python .codex/evals/report.py                          # results/ 전체
```

한 실행:

1. 임시 디렉터리에 base commit을 `git archive`로 풀고 하네스 파일 목록(`stop_gate.py`의
   `HARNESS_PATHS`에 `@AGENTS.md` shim `CLAUDE.md`·`src/mosemo/CLAUDE.md`·`tests/CLAUDE.md`를 더한 것,
   없는 경로는 건너뜀)을 지운 뒤 평가 대상 revision(`--harness`, 없으면 작업 트리의 추적·미추적 파일)에서
   채운다. `.codex/evals/`와 `tests/unit/harness/test_evals.py`를 지우고 없음을 단언한 뒤 `git init`과
   첫 커밋을 만들고 `uv sync --locked`를 실행한다. 원격·stash·`.git`을 사용자 저장소와 공유하지 않는다.
2. 부모 세션 변수를 지운 환경에 `MOSEMO_METRICS_LOG`(사용자 저장소 공통 git 디렉터리의 지표 로그)·
   `MOSEMO_HARNESS_REV`·`MOSEMO_HARNESS_DIRTY`·`MOSEMO_AGENT=claude`를 넣고, 지시문을 표준 입력으로
   `claude -p`에 넘긴다. 옵션은 `run.py`의 `claude_command`가 원본이다(`--model claude-opus-5-5`,
   `--permission-mode acceptEdits`, 허용·금지 도구 목록, `--setting-sources project`,
   `--strict-mcp-config`, `--max-budget-usd`).
3. 판정 파일 없이 `validate.sh`를 실행한 뒤 판정 파일을 복사해 판정 명령만 실행한다.
4. 결과 한 줄(작업, 실행 번호, `status`, 판정, `validate.sh` 통과, 지표 로그의 해당 `session` 줄,
   비용, CLI `num_turns`, 세션 기록에서 센 assistant 메시지 수(같은 API 메시지 id는 하나)·도구 호출 수,
   권한 거부 수, `claude --version`, `harness_rev`, `src/` 트리 해시)을 쓰고 작업 공간을 지운다.
   `-p` 결과의 `num_turns`는 에이전트 반복 횟수가 아니다(E3 실측: `num_turns` 1, 메시지 23, 도구 호출 25).
   메시지·도구 호출 수는 본 세션 기록만 세고 subagent 기록은 세지 않는다.

지우는 부모 변수: `CLAUDECODE`, `CLAUDE_EFFORT`, `CLAUDE_PID`, `CLAUDE_PROJECT_DIR`, `AI_AGENT`,
`VIRTUAL_ENV`, `CLAUDE_CODE_`·`HERDR_`·`MOSEMO_`·`GIT_` 접두사. 인증·백엔드 변수
(`ANTHROPIC_*`, `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK` 등)는 남긴다.

`gh`는 작업 공간에서 인증 없이 실패해야 한다. `GH_TOKEN`·`GITHUB_TOKEN`·`GH_ENTERPRISE_TOKEN`·
`GITHUB_ENTERPRISE_TOKEN`·`GH_CONFIG_DIR`를 지우고 실행마다 새 빈 임시 디렉터리를 `GH_CONFIG_DIR`로
넣는다. `run.py`는 `claude -p` 전에 같은 환경으로 `gh auth status`를 실행해 성공하면 실행을 멈추고,
결과 줄에 `gh_authenticated: false`를 남긴다.

허용 도구 목록은 격리 장치가 아니다. `acceptEdits`에서 목록에 없는 `python3` heredoc, 작업 공간 밖
`/tmp` 리디렉션도 거부 없이 실행됐다. 격리는 별도 git 저장소(원격 없음)와 `gh` 미인증에 기대며,
작업 공간 밖 파일 쓰기와 네트워크 접근은 막지 않는다.

## 지시 파일 로드 조건

Claude Code(2.1.319)는 프로젝트 계층(작업 공간과 그 상위 폴더)에 `CLAUDE.md`(`<폴더>/CLAUDE.md`,
`<폴더>/.claude/CLAUDE.md`)가 하나도 없을 때만 루트·하위 `AGENTS.md`를 읽는다. 작업 공간이 홈 아래에
있으면 상위 탐색에서 `~/.claude/CLAUDE.md`가 프로젝트 지시로 잡혀 `AGENTS.md`가 빠지고 사용자 지시가
들어온다. 그래서 `run.py`는 작업 공간을 홈 밖의 임시 디렉터리(macOS `$TMPDIR`, Linux `/tmp`)에 만들고,
`claude -p` 전에 작업 공간이 홈 밖이고 상위 폴더에 `CLAUDE.md`가 없음을 단언한다
(`instruction_conflicts`). 하나라도 어기면 실행을 멈춘다. 작업 공간 안의 `CLAUDE.md`는 하네스 파일 목록의
shim에서만 온다(덮어쓰기 전에 base의 것을 지운다). shim이 있으면 `AGENTS.md`는 shim을 통해 읽힌다.

## 확인한 가정(Claude Code 2.1.319, 2026-11-06, 작업 공간 `$TMPDIR/mosemo-eval-*`)

조건: 작업 공간이 홈 밖이라 상위 폴더에 `CLAUDE.md`가 없고, 부모 세션 변수를 지운 환경이다. 근거는
평가 세션 기록(`~/.claude/projects/<작업 공간>/<session>.jsonl`)의 `instructions` 첨부다.

- 부모 변수를 지운 `claude -p`가 다른 Claude Code 세션 안에서 끝까지 실행된다(키체인 OAuth 인증).
- 프로젝트 `Stop`·`PostToolUse` hook이 `-p`에서 실행되고 지표 로그에 줄을 남긴다.
- `--setting-sources project`에서 루트 `AGENTS.md`가 `Project` 지시로 로드된다. 사용자
  `~/.claude/CLAUDE.md`는 로드되지 않는다. 자동 메모리는 켜져 있지만 메모리 폴더가 작업 공간 경로별로
  새로 생기므로 비어 있어 사용자 메모리 내용은 들어오지 않는다. 다른 조건(홈 아래 작업 공간 등)에서는
  결과가 다르므로 같은 사용자 설정·같은 작업 공간 조건끼리만 비교한다.
- `--strict-mcp-config`로 MCP가 빠지고, 사용자 Skill·agent도 목록에 없다. 내장 Skill·내장 플러그인은
  남는다. 프로젝트 Skill은 `.claude/skills` 링크로 보인다.
- `acceptEdits`와 허용 목록으로 권한 요청 없이 끝난다(권한 거부 0).
- E3 한 번의 비용은 환산 $0.95였다. 실행당 상한은 $3로 둔다.

## 판정

- E1·E2: 판정 테스트 파일 전체 통과.
- E3: base commit 대비 추가된 테스트 함수가 1개 이상이고 모두 `tests/unit/observation/` 아래,
  `pytest -m "requires_db or requires_kafka" --collect-only tests/unit`이 0개, 플러그인으로
  `validate_activity`를 바꾼 상태에서 새 테스트가 1개 이상 실패.

## 비교 규칙

같은 `model`·`claude --version`끼리만 비교한다. `report.py`는 `harness_rev`·`harness_dirty`·`model`·`claude --version`·
작업별로 실행 간 최소·중앙값·최대를 낸다. 합산 점수는 만들지 않는다.
