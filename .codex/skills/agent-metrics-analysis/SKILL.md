---
name: agent-metrics-analysis
description: 소유자가 요청할 때 mosemo 에이전트 지표 로그를 지정 구간(기본 최근 7일)으로 집계·분석해 보고서를 쓸 때 사용한다.
---
# 에이전트 지표 로그 분석

## 시작과 읽을 자료
소유자가 이 Skill을 부를 때만 실행한다. 다른 Skill·Issue를 선행 조건으로 삼지 않는다. Claude Code와 Codex 모두 이 Skill을 쓴다.

읽는 자료는 다음으로 한정한다.

- 이 Skill 문서
- 집계 JSON(아래 3단계 명령의 표준 출력)
- `interval.session_files`에 있는 세션 기록
- `~/mosemo-reports/`의 직전 보고서 하나

## 절차
1. 구간 정하기: 소유자가 지정한 구간을 쓴다. 날짜로 지정하면 Asia/Seoul 시작일 00:00부터 끝날 다음 날 00:00까지다. 지정이 없을 때만 실행 시각 기준 최근 7일 `(실행 시각 - 7일, 실행 시각]`이다. 실행 시각은 `date -u +%Y-%m-%dT%H:%M:%SZ`, 기본 시작 시각은 `date -u -v-7d +%Y-%m-%dT%H:%M:%SZ`(macOS)로 얻는다. 시작·끝은 시간대가 붙은 ISO 8601(`Z` 또는 `+09:00`)로 넘긴다. 지정한 구간을 해석할 수 없으면 집계하지 않고 소유자에게 구간을 다시 묻는다. 7일 추세는 `aggregate.py`가 끝 시각의 Asia/Seoul 날짜를 포함한 7일로 고정해 내므로, 구간이 7일이 아니면 「실행 정보」에 그 사실을 적는다.
2. 경로 정하기(읽기 전용 명령만):
   - 지표 로그: `printenv MOSEMO_METRICS_LOG` 값이 있으면 그 값, 없으면 `git rev-parse --path-format=absolute --git-common-dir` 출력 아래 `mosemo-agent-metrics.jsonl`.
   - 저장소 본체: 공통 git 디렉터리의 부모.
   - 홈: `printenv HOME` 값.
   - worktree 루트: 홈 아래 `.herdr/worktrees/mosemo-server`.
3. 집계 실행: 저장소 루트에서 아래 명령의 대문자 자리(`LOG`, `START`, `END`, `HOME_DIR`, `MAIN`, `WORKTREES`)에 1·2단계에서 얻은 값을 직접 넣어 한 줄로 실행하고 표준 출력 JSON을 읽는다. 셸 변수는 호출 사이에 남지 않으므로 쓰지 않는다. 출력은 파일로 저장하지 않는다. 실제로 실행한 명령은 「실행 정보」에 적는다.

   ```sh
   uv run --locked python .codex/reports/aggregate.py --log LOG --start START --end END --home HOME_DIR --main-checkout MAIN --worktrees-root WORKTREES
   ```

   명령이 0이 아닌 코드로 끝나거나(지표 로그 없음 포함) 출력이 JSON이 아니면 보고서 파일을 쓰지 않고, 오류 한 줄과 실행한 명령을 대화에 보고하고 끝낸다.
4. 숫자 표 옮기기: 집계 JSON 값을 「보고서 형식」의 대응표대로 그대로 옮긴다. 더하기·빼기·백분율 변환 등 어떤 계산도 하지 않고(편집 통과율 `pass_rate`도 소수 그대로), 행 순서는 JSON의 순서를 따른다. 값이 없거나 `null`이면 JSON 값 그대로 적는다. 활동 세션이 0이어도 보고서를 쓴다.
5. 판단 분류: `interval.session_files`에 있는 세션 기록 파일만, 파일 경로를 지정한 검색(`Grep` 또는 `rg PATTERN FILE`)으로 좁힌 뒤 필요한 부분만 읽는다. `interval.panel_unreadable_sessions`의 세션은 패널 리뷰 내용을 같은 방법으로 읽어 분류한다. 지금 이 분석을 하는 세션의 기록은 판단 분류에서 뺀다(숫자 표는 JSON 그대로 둔다). 「판단 기준」의 네 분류로 나누고 판단값이라고 표시한다.
6. 이전 보고서 비교: `~/mosemo-reports/`의 보고서를 (날짜, 시각 접미사. 접미사가 없으면 `000000`) 순으로 놓고 이번 파일 바로 앞의 하나를 읽는다. 2절에 두 보고서의 같은 항목 값(규칙별 위반 건수, 편집 통과율, 판단 분류 건수)과 두 구간을 나란히 적는다. 차이는 계산하지 않는다. 없으면 「이전 보고서 없음」이라고 적는다.
7. 보고서 쓰기: `mkdir -p ~/mosemo-reports`로 폴더를 만들고, Asia/Seoul 실행 날짜로 `2026-11-14.md` 형식 파일 하나를 쓴다. 같은 이름이 있으면 덮어쓰지 않고 `2026-11-14-093000.md`처럼 Asia/Seoul 실행 시각(`HHMMSS`)을 붙인다. Codex 샌드박스에서는 저장소 밖 쓰기에 승인 요청이 뜰 수 있다.
8. 대화에 요약: 보고서 경로, 구간, 활동 세션 수, 3절의 개선점 제목을 대화에 적는다.

## 판단 기준
- 사용자 정정: 사용자가 에이전트의 직전 행동·결과를 되돌리거나 바로잡으라고 한 메시지.
- 권한 거부: 도구 호출이 사용자·정책에 의해 거부된 기록.
- 같은 실패의 반복: 같은 세션에서 같은 오류·같은 hook 차단 사유가 2회 이상 나온 것.
- 리뷰 지적 종류: 리뷰 보고의 지적을 정확성·계약·테스트·책임 위치·보안 등 내용으로 나눈 것.
- 반복 문제: 같은 규칙·같은 분류가 2일 이상 또는 2세션 이상 나온 것.
- 에이전트 간 비교: `interval.by_agent_model`의 키(`agent/model`) 중 `edit.lines`가 0인 키가 있으면 편집 통과율을 키 사이에서 비교하지 않고 그 키에 「비교 불가(편집 줄 없음)」로 적는다. 근거: Codex `PostToolUse`가 `exec` 안 `apply_patch`에 발화하는지 확인되지 않았다.
- 세션 구분: `interval.by_entrypoint`의 `non_interactive`는 실험 세션과 비대화형 개발 세션(Aico 등)을 모두 담는다. 활동 세션 수에서 빼지 않고, 비대화형이라는 이유로 판단 분류에서 빼지 않는다. `unknown`은 세션 기록을 찾지 못했거나 분류 표에 없는 원문(`entrypoints` 참조)이다.
- 버전 비교: `trend.by_harness_rev`의 항목은 `harness_rev` 단위로만 나란히 적는다. `dirty_lines`가 0보다 큰 항목은 「작업 중 하네스 변경 포함」으로 표시한다.
- 기대 행동·전체 검증: `interval.expected_actions`와 `interval.verify`는 에이전트 키 단위로만 읽고 에이전트 사이 값을 합치지 않는다. `codex`의 Skill 규칙(`expect`가 `skill:`로 시작)은 #226 전 Codex `stop` 줄의 `skills`가 비어 `missing`이 부풀려질 수 있다. Codex 셸의 세션 ID 변수가 확인되지 않아 Codex의 전체 검증은 `verify`의 `unknown` 키에 쌓이고, 그래서 `codex`의 `src_tests_edit_verify`도 `missing`이 부풀려질 수 있다.
- 명령 사건의 한계: `pr_create`·`plan_issue_create` 사건은 명령 문자열 검사라 명령 안에 인용한 문자열(Claude `Bash` heredoc 본문·검색 패턴, `*** End Patch` 없는 잘린 Codex 패치 본문)도 사건으로 세어 `missing`이 부풀려질 수 있다. 이 두 규칙의 `missing_sessions`는 세션 기록에서 실제 PR·Plan Issue 생성 명령인지 확인한 뒤 개선점 근거로 쓴다.
- 검증 충족: `src_tests_edit_verify`는 같은 세션의 `verify` 줄 중 `at`이 그 세션의 구간 안 마지막 사건 이후이고 구간 `end` 이하인 줄만 충족으로 친다. 구간 끝 뒤에 돌린 검증은 이번 구간에서 미충족이다.
- 비교·대응은 JSON 키 단위로만 한다. 키를 합치거나 차이를 내지 않는다.
- 기계 지표 근거 문제를 판단값 근거 문제보다 먼저 쓴다.

## 보고서 형식
세 절 제목과 `## 실행 정보`는 아래 문자열 그대로, 이 순서로 쓴다. 제목 줄 뒤에는 Asia/Seoul 실행 날짜를 적는다.

```markdown
# mosemo 에이전트 지표 로그 분석 2026-11-14

## 1. 결과
이번 구간 숫자 표(아래 대응표의 `interval` 항목), 세션 구분(대화형·비대화형·미상)별 표

판단값

| 분류 | 건수 | 세션 ID | 예시 한 줄 |
| --- | --- | --- | --- |

## 2. 최근 며칠의 양상
7일 일별 표, `harness_rev`별 표, 반복 문제, 이전 보고서 비교

## 3. 개선해야 할 점
반복 문제마다 근거(지표 값, 세션 ID)와 바꿀 하네스 위치(파일 경로, 규칙·절), 장치 종류와 더 앞 장치를 고르지 않은 이유, 해당하면 평가 문제 후보

## 실행 정보
구간(`window.start`·`window.end`), 활동 세션 수, 실제로 실행한 집계 명령, 분석 에이전트와 모델, `skipped_lines`, 비교한 이전 보고서 경로(없으면 없음)
```

3절 개선점마다 아래 줄을 쓴다.

- `장치 종류:` `검사`, `hook`, `Skill·문서`, `평가 문제` 중 하나. 우선순위는 `검사 > hook > Skill·문서 > 평가 문제`이고, 고른 장치보다 앞 장치를 고르지 않은 이유를 한 줄로 적는다(`검사`면 생략).
- `평가 문제 후보:` 사용자 정정 반복과 `interval.expected_actions`의 Skill 미호출(`missing`)에서 나온 개선점에만 한 줄. 새로 쓴 프롬프트와 `should_trigger`(`true`·`false`)를 적고, 세션 원문을 옮기지 않는다.

보고서 항목과 집계 JSON 키의 대응:

| 보고서 항목 | JSON 키 |
| --- | --- |
| 활동 세션 수 | `interval.sessions` |
| 새 세션 수 | `interval.new_sessions` |
| 모델이 바뀐 세션 수 | `interval.model_changed_sessions` |
| 차단 | `interval.blocks` |
| `capped` | `interval.capped` |
| 편집 줄·통과 줄·편집 통과율 | `interval.edit.lines`·`interval.edit.clean_lines`·`interval.edit.pass_rate`(소수 그대로) |
| 에이전트·모델별 세션 | `interval.sessions_by_agent_model` |
| 에이전트·모델별 값 | `interval.by_agent_model`(키마다 `blocks`·`capped`·`edit`·`rules`·`stop_results`) |
| 세션 구분별 값 | `interval.by_entrypoint`(키 `interactive`·`non_interactive`·`unknown`마다 `entrypoints`·`sessions`·`blocks`·`capped`·`edit`·`rules`) |
| Stop 결과 분포 | `interval.stop_results` |
| 규칙별 편집·Stop 건수와 세션 수 | `interval.rules.edit`·`interval.rules.stop`(규칙마다 `count`·`sessions`) |
| 리뷰 | `interval.reviews[]`의 `reviewer`·`source`·`reports`·`format_ok`·`findings` |
| 7일 일별 표 | `trend.days[]`의 `date`·`sessions`·`blocks`·`capped`·`edit.pass_rate`, 규칙마다 `rules.edit`·`rules.stop`의 `count` |
| `harness_rev`별 값 | `trend.by_harness_rev[]`의 `harness_rev`·`first_at`·`last_at`·`sessions`·`dirty_lines`·`blocks`·`capped`·`edit.pass_rate`, 규칙마다 `rules.edit`·`rules.stop`의 건수 |
| 기대 행동 | `interval.expected_actions`(에이전트 키마다 규칙 ID별 `expect`·`sessions`·`missing`·`missing_sessions`·`no_stop`) |
| 전체 검증 | `interval.verify`(에이전트 키마다 `runs`·`ok_runs`·`failed_checks`·`not_run`) |
| 건너뛴 로그 줄 | `skipped_lines` |

예시 한 줄에 비밀값(토큰, 키, `.env` 값)을 옮기지 않는다.

## 금지
- 파일 쓰기는 보고서 파일 하나와 보고서 폴더 만들기뿐이다. 집계 JSON·중간 결과도 파일로 남기지 않는다.
- 저장소 파일·Git 상태를 바꾸지 않는다(`git` 쓰기 명령, 커밋, 브랜치·worktree 조작 금지). git은 읽기 전용 `git rev-parse`만 쓴다. 그 밖의 셸 명령은 `date`, `printenv`, `mkdir -p ~/mosemo-reports`, `ls ~/mosemo-reports`, 위 집계 명령, `session_files`의 파일을 지정한 검색·읽기만 쓴다.
- `gh`, `git push`, 외부 게시, 보고서를 이 Mac 밖으로 보내는 일을 하지 않는다(`jolvy/mosemo`는 공개 저장소).
- 하네스(`AGENTS.md`, Skill, agent, hook, 검사기)를 직접 고치지 않는다. 3절에 제안만 쓴다.
- `interval.session_files`에 없는 세션 기록(다른 프로젝트 등)은 읽지 않는다. 경로를 지정하지 않은 `Grep`·`Glob`이나 `rg`로 `~/.claude/projects/`·`~/.codex/sessions/`를 훑지 않는다.

## 종료 조건
- 보고서 파일 하나를 쓰고 대화에 요약했으면 끝난다. 3절 개선점을 구현하거나 Issue로 만들지 않는다(소유자가 결정한다).
- 3단계 집계가 실패했으면 보고서 파일 없이 오류 보고로 끝난다.
