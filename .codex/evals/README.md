# Skill 호출 평가

문제 파일 하나(`cases.csv`)와 판정기 하나(`run.py`)로 Claude Code와 Codex가 기대한 Skill을 부르는지 같은 기준으로 잰다. 계획 원본은 #238 T6이다.

## 사용법

```sh
uv run --locked poe eval                  # 모든 문제
uv run --locked poe eval --case pr-draft  # 문제 하나
```

- 실행 단위는 문제 × 에이전트(`claude`, `codex`)다. 일반 문제는 3회, 양성 대조(`id`가 `control-`로 시작)는 1회 실행한다.
- 프롬프트는 표준 입력으로 넘기고, 작업 폴더는 명령을 실행한 체크아웃의 루트다.
- 결과는 실행마다 `results/UTC_STAMP/results.jsonl`에 한 줄씩 쓰고(`.gitignore` 대상), 끝에 문제·에이전트별 요약표를 출력한다. 외부에 게시하지 않는다.
- 종료 코드는 모든 실행의 `triggered`가 `null`이 아니고 양성 대조가 모든 실행에서 `true`일 때 0이다. 일반 문제의 결과가 기대와 다른 것은 평가 결과이지 실행기 실패가 아니다.

## 문제 형식

`cases.csv`의 열은 `id,skill,should_trigger,prompt`다. `skill`은 `.codex/skills/` 아래 폴더 이름이다. 프롬프트는 새로 쓰고 세션 기록·비밀값을 넣지 않는다(공개 저장소).

## 판정

- Claude: `stream-json` 줄을 `stop_metrics.transcript_facts`로 읽어 `Skill` 호출과 `Read`한 `SKILL.md`를 찾는다.
- Codex: `--json` 줄 중 `item.completed`이고 `item.type`이 `command_execution`인 이벤트의 `item.command`에서 `.codex/skills/이름/SKILL.md`·`.claude/skills/이름/SKILL.md`를 찾는다.
- 찾은 이름이 문제의 `skill`과 같을 때만 `triggered`가 `true`다. 비정상 종료나 기록을 읽지 못한 실행은 `triggered`가 `null`이고 `error`에 사유가 남는다.

## 비용

소유자 인증으로 실행되고 비용이 든다. Claude는 실행마다 `--max-budget-usd 0.5`이고 현재 문제로 4회라 최대 2달러다. Codex는 달러 상한 옵션이 없어 실행 수로만 제한한다. 상한이 모자라 `null`이 나오면 상한을 올리지 않고 결과를 근거로 계획을 고친다.

## 읽기 전용·hook·MCP 제외 이유

- 읽기 전용: 호출 여부만 보므로 파일을 쓰지 않는다. Claude는 `--permission-mode dontAsk`와 `--allowedTools Read,Grep,Glob,Skill`로 허용 목록 밖 도구를 거부하고, Codex는 `--sandbox read-only`로 실행한다. 저장소 사본·`git init`·worktree를 만들지 않고 git 설정이나 인증을 바꾸지 않는다.
- hook 끄기: 저장소 Stop hook이 부모 작업 트리의 파일을 다시 쓰거나 차단 지시로 비용을 쓰지 않도록 Claude는 `--settings '{"disableAllHooks": true}'`, Codex는 `--disable hooks`로 끈다.
- MCP 제외: 읽기 전용 설정은 MCP 호출의 외부 효과를 막지 못한다. Claude는 `--strict-mcp-config`, Codex는 `--ignore-user-config`로 MCP 서버를 싣지 않는다. 그래서 Codex는 모델과 추론 강도를 명시한다.
- 기록 남기지 않기: Claude는 `--no-session-persistence`, Codex는 `--ephemeral`로 세션 기록을 남기지 않아 지표 분석에 섞이지 않는다.
- 자식 환경: 부모 세션 표식(`CLAUDECODE`, `CLAUDE_PID`, `CLAUDE_CODE_`로 시작하는 변수)만 지운다. 사용자 CLAUDE.md·사용자 Skill은 실제 개발 세션과 같은 문맥을 재기 위해 그대로 둔다.
