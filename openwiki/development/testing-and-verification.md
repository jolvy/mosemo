---
type: workflow
title: 테스트와 검증
description: 테스트 분류(Unit·Integration·E2E)와 위치, Docker가 필요한 requires_db·requires_kafka 마커, poe 작업, 커밋 전 검증 명령과 커버리지 기준, CI Quality job 단계를 정리한다.
tags: [testing, pytest, coverage, ci, verification]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-22T13:31:20.917Z
sources:
  - id: openwiki-source-3c6b76d80d7f929dde842165
    resource: repo://.codex/skills/verification/scripts/ci_gate.py
  - id: openwiki-source-8fc10a4767cd01fd40612574
    resource: repo://.codex/skills/verification/scripts/validate.py
  - id: openwiki-source-81b78b1a4f671cb4cd88f0ff
    resource: repo://.codex/skills/verification/scripts/validate.sh
  - id: openwiki-source-164e2da859b5277df81c7d94
    resource: repo://.github/workflows/ci.yml
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-eedd75e99cec32f986d7b768
    resource: repo://docs/label-engine-eval.md
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-1c9ddf72032c2bd4da068cd8
    resource: repo://scripts/check_coverage.py
  - id: openwiki-source-e8577da5ba062a695378f268
    resource: repo://scripts/label_eval_cases.py
  - id: openwiki-source-54be5439a09872ff7ef8f145
    resource: repo://scripts/label_eval_run.py
  - id: openwiki-source-8a0d68881049fe3690cd258c
    resource: repo://scripts/label_eval_score.py
  - id: openwiki-source-5063b6aa8934c32dd8a94ee1
    resource: repo://tests/AGENTS.md
  - id: openwiki-source-f0a6e7dc03522b2682f88655
    resource: repo://tests/conftest.py
  - id: openwiki-source-d5a25b3d746855d816062422
    resource: repo://tests/integration/conftest.py
  - id: openwiki-source-5cf11d64a14b1fda1cc4bc4c
    resource: repo://tests/support/acceptance.py
  - id: openwiki-source-7963bb1002d4efe2b24fef6a
    resource: repo://tests/unit/harness/test_tests_layout.py
  - id: openwiki-source-3a7152c9aed17d92a34fc06a
    resource: repo://tests/unit/scripts/test_label_eval_docs.py
generated: { by: "claude-code", at: "2026-11-22T13:31:20.917Z" }
---

# 테스트와 검증

원본은 [tests/AGENTS.md](../../tests/AGENTS.md)(테스트 분류·위치)와 루트 [AGENTS.md](../../AGENTS.md) 「빌드와 검증」이다. 이 페이지와 다르면 원본이 맞다.

## 테스트 분류와 위치

| 분류 | 위치 | 기준 |
| --- | --- | --- |
| Unit | `tests/unit/{패키지}/` | 하나의 논리적 단위(SUT)의 동작. 복잡한 의존성은 Fake·Mock·Stub으로 격리할 수 있다 |
| Integration | `tests/integration/{패키지}/` | 실제 구성요소 사이 연동, 또는 컴포넌트를 공개 인터페이스(HTTP·메시지·명령어·신호)로 호출하는 검증. Repository·프로세스 수명주기 테스트 포함 |
| E2E | `tests/e2e/`(패키지 폴더 없이 시나리오 파일을 바로 둔다) | PRD의 핵심 업무 시나리오를 테스트가 띄운 서버 프로세스에 실제 HTTP로 검증 |

- `{패키지}`는 SUT가 있는 `src/mosemo`의 최상위 패키지(`identity`, `labels`, `observation`, `labeling`, `analytics`, `shared`, `platform`, `entrypoints`)다. 예외로 저장소 도구 테스트 `harness`(`.codex`), `scripts`, 마이그레이션 테스트 `migrations`, 테스트 지원 코드를 검증하는 `support`(`tests/support`), 저장소 도구 테스트 `tools`(`tools/`와 측정 도구 `infra/measurement/`)가 있다.
- E2E의 컨테이너·프로세스 fixture는 `tests/e2e/conftest.py`(`e2e_system` 등), 프로세스 기동·종료와 설정은 `tests/support/e2e.py`에 있다. 현재 시나리오는 `tests/e2e/test_activity_analytics.py`다. 테스트가 API·Consumer 프로세스를 빈 포트에 띄우고 끝나면 SIGTERM으로 끄며, 배치는 단계마다 `python -m mosemo.entrypoints.batch run JOB_NAME`으로 한 번씩 돌린다(`serve` 금지). 외부 네트워크(Kakao, AI 모델)는 호출하지 않는다. 활동 배치 HTTP 경로가 24시간 수락 기간 밖의 원본을 거절하므로 E2E-1은 최근 하루 안에서 업로드하고, 지난 날짜의 일간 통계는 API-5에 맡긴다.
- 고정된 과거 시각의 원본을 보내는 테스트는 `tests/support/acceptance.py`의 `acceptance_clock` fixture를 요청해 수락 시각만 바꾼다(`now` 설정). 요청하지 않은 테스트는 실제 시계를 쓰며, legacy 투영 시계는 바뀌지 않는다.
- 공유 테스트 코드는 `tests/support/`에 둔다.
- 구조 규칙은 `tests/unit/harness/test_tests_layout.py`가 강제한다.
- 활동 분석의 독립 참조 구현은 `tests/support/analytics_reference/`에 있고 `tests/unit/support/analytics_reference/`가 검증한다. `tools/analytics_protocol_models/`는 `tests/unit/tools/`가 검증한다. 자세한 내용은 [활동 분석](../domain/activity-analytics.md)을 본다.

## Docker가 필요한 테스트 마커

- `requires_db`(실제 PostgreSQL)와 `requires_kafka`(실제 Kafka)는 [tests/conftest.py](../../tests/conftest.py)가 fixture 사용 여부를 보고 자동으로 붙인다. 손으로 붙이지 않는다.
- 컨테이너는 testcontainers로 띄운다([tests/integration/conftest.py](../../tests/integration/conftest.py)). 그래서 이 마커가 붙은 테스트를 돌릴 때는 Docker가 동작 중이어야 한다.
- Docker 없이 돌리려면 `-m "not requires_db and not requires_kafka"`를 붙인다.
- 새 컨테이너 fixture를 추가하면 `tests/conftest.py`의 `CONTAINER_MARKERS` 매핑도 함께 추가한다. 매핑에는 통합 테스트용(`integration_*`), 재시작 가능한 DB(`restartable_database`), E2E용(`e2e_*`) fixture가 있다.

## 기본 실행에서 빠지는 테스트(FI·E2E)

- `e2e` 마커는 `tests/conftest.py`가 `tests/e2e/` 아래 테스트에 경로로 자동으로 붙인다.
- `fault_injection`은 실행 비용 마커(프로세스 kill·DB 재시작 장애 주입)다. `tests/integration/analytics/test_fault_injection*.py` 모듈에만 `pytestmark`로 손으로 붙이고, 그 파일에는 다른 테스트를 두지 않는다.
- `poe test`·`poe coverage`와 CI `Tests` job은 `-m "not fault_injection and not e2e"`로 두 종류를 뺀다.
- 로컬 직접 실행: FI만 `uv run --locked poe test -q tests -m fault_injection`, E2E만 `... -m e2e`, 전부 `... -m ""`. pytest는 마지막 `-m`만 쓰므로 `poe test`에 다른 `-m`을 붙이면 기본 제외가 사라진다. Docker 없이 돌리면서 제외를 유지하려면 `-m "not fault_injection and not e2e and not requires_db and not requires_kafka"`처럼 전체 식을 쓴다.

## poe 작업

| 작업 | 하는 일 |
| --- | --- |
| `poe test` | `pytest -v -m "not fault_injection and not e2e"`. 커버리지 기준 없이 일부 테스트를 돌릴 때 쓴다 |
| `poe coverage` | 같은 제외 식으로 `pytest --cov` 뒤 `scripts/check_coverage.py`로 기준을 판정 |
| `poe lint-imports` | import-linter 계약 검사 |
| `poe openapi` | `openapi/openapi.json` 스냅샷 갱신 |
| `poe ci` | `act`로 pull request Quality job을 로컬에서 실행 |

`poe test`·`poe coverage`만 `.env.example`을 먼저 읽고, 있으면 `.env`, `.env.test` 순서로 덮어쓴다.

## 라벨 제안 평가 입력 스크립트

[scripts/label_eval_cases.py](../../scripts/label_eval_cases.py)는 라벨 제안 엔진 평가(Plan #309)의 입력을 만드는 CLI다. 앱 코드가 아니라 `scripts/`의 저장소 도구이며 `tests/unit/scripts/`·`tests/integration/scripts/`가 검증한다. 현재 시각을 읽지 않고, 개인 활동이 들어 있으므로 결과를 저장소 안에 쓰지 않는다.

- `snapshot`: `--account-id`, `--from`, `--to`, `--as-of`, `--out`을 받아 `--as-of` 시점의 읽기 전용 트랜잭션(`read_snapshot`)에서 관찰 타임라인을 읽고, 기간 `[from, to)`에 끝난 닫힌 상세 구간(`CLOSED_DETAILED_ACTIVITY`)만 종료 시각·활동 ID 순으로 JSONL에 쓴다. `--to`가 `--as-of`보다 늦으면 거절한다. `--as-of` 시점에 진행 중인 구간은 빠지므로 재실행 결과가 같다.
- `cases`: 스냅샷(`--activities`)에 persona 규칙 TOML(`--persona`, 반복 가능, 예시는 `scripts/label_eval_personas/example.toml`)을 적용해 정답 라벨(`gold`)을 매기고, 각 사례 시점에 있었을 few-shot 예시를 다시 만든다. 예시는 엄격히 앞선 사례만 최신순으로 `MAX_EXAMPLES`개까지 쓴다(운영 retriever와 같은 방식). 사례에는 `cold_start`(예시 3개 미만)·`gold_null`·`new_host`·`personas_disagree` 태그가 붙는다. 소유자 라벨은 `--owner-labels`와 `--owner-label-names`를 함께 줄 때만 `owner` persona 사례로 더해진다.
- 출력은 `write_jsonl`이 `{path}`와 `{path}.sha256`을 0600 임시 파일로 만든 뒤 교체해 쓴다. 심볼릭 링크를 따라가지 않고, 다이제스트를 못 쓰면 본문도 지운다.
- 잘못된 인자·입력(알 수 없는 키, 중복 활동 ID, 예약어 persona 이름 등)은 `InputError`로 `rejected: ...`를 stderr에 내고 종료 코드 2로 끝나며 아무것도 쓰지 않는다.

단위 테스트는 `tests/unit/scripts/test_label_eval_cases.py`(스냅샷 행·사례·파일 쓰기)와 `test_label_eval_personas.py`(persona 파싱·검증)이고, `tests/integration/scripts/test_label_eval_snapshot.py`는 실제 PostgreSQL에서 범위 안 닫힌 구간만 담기는지, 진행 중 구간이 빠지고 재실행이 같은지, `--to`가 `--as-of` 뒤면 출력 없이 거절되는지를 확인한다.

### 평가 실행: `label_eval_run.py`

[scripts/label_eval_run.py](../../scripts/label_eval_run.py)는 `cases`가 만든 사례 파일에 라벨 제안 엔진을 돌려 사례마다 결과 행 하나를 `--out` JSONL에 덧붙인다(Plan #309). 호출은 한 번에 하나씩 순서대로 실행한다.

- 엔진(`--engine`)은 `a`(사례당 한 번 호출, `--batch-size 1`만 허용), `a-batch`(여러 사례를 한 호출로 처리, 배치 크기 최대 50), 결정 모델 `b`·`b-none`(OpenAI Decisions), `c`·`c-none`(TypeSafe Jev), 점검용 `oracle`·`random`이다. `-none` 엔진은 선택지에 "라벨 없음"을 더해 null 예측을 허용한다. `a`·`a-batch`는 `LABEL_MODEL`을 `<provider>:<model>`(provider는 `openai`·`google`) 형식으로 요구하며, 값이 틀려도 키가 노출되지 않도록 값을 출력하지 않는다.
- 결정 모델 엔진(`DecisionsEngine`·`JevEngine`)은 `httpx2`로 호출당 요청 하나를 보내고 재시도하지 않는다(`max_attempts = 1`). 결과 행은 `confidence`, `prob`, `refused`, 경로가 주는 비용(`cost_usd`)을 담는다. `b`는 `OPENAI_API_KEY`를 읽는다. `c`는 `--c-route`(`aimlapi`·`openrouter`·로컬 시험용 `ollama`)가 필수이고 행의 엔진 이름은 `c@<route>`다. 경로별 키(`AIMLAPI_API_KEY`·`OPENROUTER_API_KEY`, ollama는 키 없음)는 헤더로만 쓰며, 헤더로 보낼 수 없는 값(비ASCII·제어 문자·앞뒤 공백)은 호출 전에 변수 이름만 밝히고 거절한다. `c` 계열이 아닌 엔진에 `--c-route`를 주면 거절한다.
- 재개: 오류 없는 행이 이미 있는 키 `(case_id, engine, batch_size, rep)`는 다시 호출하지 않고, 오류 행만 있는 키는 다시 호출해 새 행을 남긴다. 호출은 persona별로 사례 파일 순서대로 묶는다.
- 예산 보호: `oracle`·`random`을 뺀 모든 엔진은 `--max-requests`가 필수이며, 남은 호출 수 × 호출당 최대 시도 횟수(openai 9, google 3, 결정 모델 엔진 1)가 그 값을 넘으면 호출 전에 종료 코드 2로 거절한다. `--min-interval-ms`로 호출 시작 간격을 둔다.
- 실패 처리: 모델·HTTP·타임아웃 실패와 스키마에 맞지 않는 응답(`ValidationError`)·질문과 맞지 않는 답(`ResponseError`)은 예외 타입 이름만 담은 오류 행이 되고, 그 밖의 예외는 실행을 멈추되 이미 쓴 행은 남아 재실행이 이어받는다.
- 테스트는 `tests/unit/scripts/test_label_eval_run_control.py`(재개·예산·페이싱·CLI)와 `test_label_eval_run_engines.py`(엔진)이고, 공유 보조 코드는 `tests/support/label_eval.py`다.

### 점수와 판정: `label_eval_score.py`

[scripts/label_eval_score.py](../../scripts/label_eval_score.py)는 `cases`의 사례 파일(`--cases`)과 실행 스크립트가 덧붙인 결과 JSONL(`--results`)을 합쳐 `--out` 폴더에 `summary.json`과 `report.md`를 쓴다(Plan #309). 같은 입력과 `--seed`는 같은 바이트를 만들고, 통계는 표준 라이브러리만 쓴다. 인자나 입력이 거절되면 아무것도 쓰지 않고 종료 코드 2로 끝난다.

- 엔진·배치 크기별 지표를 내고, `(a, 배치 1, rep 0)`을 기준선으로 같은 사례 범위(same coverage)에서 비교한다. 선택 입력은 `--prices`(가격 TOML), `--latency-results`(지연 시간 전용 결과), `--decision`(미리 정해 둔 판정 기준 TOML)이다.
- `--decision`을 주면 기준에 정한 primary 엔진마다 batch 1 비교로 판정한다. 판정 순서는 reject(정확도 차 신뢰구간 상한이 `min_accuracy_diff` 미만이거나 p95 지연·정답당 비용이 상한 초과) → adopt(신뢰구간 하한이 `min_accuracy_diff` 이상이고 비용과 p95가 모두 있음) → inconclusive다. 비용이나 p95가 비어 있으면 adopt만 막는다.
- 결론은 adopt된 엔진 중 신뢰구간 하한이 가장 높은(동률이면 정답당 비용이 낮은) 엔진 이름이다. adopt가 없으면 inconclusive가 하나라도 있을 때 `판단 불가`, 아니면 `A 유지`다. primary가 여러 개면 신뢰 수준을 `1 − 0.05 / primary 수`로 보정한다.
- 테스트는 `tests/unit/scripts/test_label_eval_score_metrics.py`(지표·비교)와 `test_label_eval_score_decision.py`(판정·CLI)이고 보조 코드는 `tests/support/label_eval_score.py`다.

### 사용 가이드: `docs/label-engine-eval.md`

[docs/label-engine-eval.md](../../docs/label-engine-eval.md)는 위 세 스크립트를 스냅샷 → 사례 생성 → 실행 → 채점 순서로 쓰는 절차와 옵션 표, 엔진·C 경로 표를 담은 사용 가이드다. 평가 디렉터리는 개인 활동이 들어 있으므로 저장소 밖에 두고, `snapshot`·`cases`는 `--out`이 git work tree 안이면 종료 코드 2로 거부한다. 가이드의 옵션 표가 실제 CLI와 어긋나지 않도록 `tests/unit/scripts/test_label_eval_docs.py`가 각 명령의 `--help` 옵션 집합(`--help` 제외)과 가이드 표의 옵션 열을 비교한다. 스크립트 옵션을 바꾸면 가이드 표도 함께 고쳐야 이 테스트가 통과한다.

## 커밋 전 검증

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked complexipy src --max-complexity-allowed 15 --isolated --no-ignore
uv run --locked ty check
uv run --locked poe lint-imports
uv build
uv run --locked poe coverage
git diff --check
git diff --cached --check
```

- 한 번에 돌리려면 `bash .codex/skills/verification/scripts/validate.sh`를 쓴다. 위 명령에 문서·아키텍처·코드 규칙 검사(`validate_docs.py`, `validate_architecture.py`, `validate_conventions.py`)를 더해 실행하고 마지막 줄에 `VERIFY_RESULT` JSON으로 판정한다.
- 커버리지 기준은 라인 95%, 브랜치 90%이며 반올림 없이 판정한다. 기준값은 [scripts/check_coverage.py](../../scripts/check_coverage.py)에만 둔다.
- complexipy는 기준선 파일 없이 `src`의 모든 함수를 검사하고 인지 복잡도 15를 넘으면 실패한다.
- API 스키마나 설명이 바뀌면 `uv run --locked poe openapi`로 스냅샷을 갱신한다.

## CI

[.github/workflows/ci.yml](../../.github/workflows/ci.yml)은 pull request와 main push에서 다음 job을 돌린다.

| job | 하는 일 |
| --- | --- |
| `Detect changes`(`changes`) | `ci_gate.py changes`로 main 대상 PR이 `openwiki/` 아래만 바꿨는지 판정(`openwiki_only`) |
| `Static checks`(`static`) | 문서·아키텍처·코드 규칙 검사 → complexipy → Ruff lint·format → ty → import 계약 → OpenAPI 스냅샷 테스트 |
| `Tests`(`tests`) | `pytest -q tests --cov -m "not fault_injection and not e2e"`와 커버리지 기준 판정. openwiki만 바뀌면 건너뜀 |
| `Quality` | 필수 검사 이름을 유지하는 집계 job. `if: always()`로 돌며 `ci_gate.py verdict`가 `changes`·`static` 성공, `tests` 성공 또는 openwiki 전용 건너뜀을 확인 |
| `Fault injection tests` | `-m fault_injection`. openwiki만 바뀌면 건너뜀 |
| `E2E tests` | `pytest -q tests/e2e -m e2e`. openwiki만 바뀌면 건너뜀 |

워크플로 `concurrency`는 같은 workflow·ref 그룹에서 이전 실행을 취소하되, `cancel-in-progress`는 `pull_request` 이벤트일 때만 참이다. main push 실행은 이후 push에 취소되지 않는다.

[ci_gate.py](../../.codex/skills/verification/scripts/ci_gate.py)는 `git diff --name-only --no-renames -z HEAD^1 HEAD`(PR merge commit 기준)로 변경 경로를 구한다. 판정이 틀리면 전부 실행하는 쪽이 안전하므로 대소문자를 구분하고, pull_request가 아니거나 base가 `main`이 아니거나 변경 경로가 없으면 `openwiki_only=false`다. 러너 기본 `python3`로 돌기 때문에 표준 라이브러리만 쓴다.

## OpenWiki paths 검사

[.github/workflows/openwiki-paths.yml](../../.github/workflows/openwiki-paths.yml)은 `pull_request_target`에서 `check_openwiki_paths.py`를 실행해, `openwiki/update` 브랜치가 아닌 PR이 `openwiki/INSTRUCTIONS.md` 밖의 `openwiki/` 생성물을 바꾸면 실패시킨다. 스크립트 자체는 `tests/unit/harness/test_check_openwiki_paths.py`가 검증한다. 규칙은 [Engineering Harness 작업 흐름](engineering-harness.md)을 본다.

## 관련 페이지

- [빠른 시작](../quickstart.md)
- [Engineering Harness 작업 흐름](engineering-harness.md)
- [활동 분석](../domain/activity-analytics.md)
