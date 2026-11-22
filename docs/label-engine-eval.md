# 라벨 제안 엔진 평가 하네스

라벨 제안 엔진 후보를 같은 입력으로 비교하는 오프라인 평가 도구의 사용법이다. 후보는 현재 엔진 A(PydanticAI + `LABEL_MODEL`), B(OpenAI Decisions API), C(TypeSafe Jev, 중계 경유)이고, 점검용으로 `oracle`·`random`이 있다. 계획 원본과 판정 규칙은 Plan Issue #309다. 스크립트는 운영 코드(`src/mosemo/`)를 바꾸지 않고 기존 테이블을 읽기만 한다.

| 단계 | 스크립트 | 입력 | 출력 |
| --- | --- | --- | --- |
| 스냅샷 | `scripts/label_eval_cases.py` `snapshot` | `DB_*` 설정의 PostgreSQL | `activities.jsonl`, `activities.jsonl.sha256` |
| 사례 생성 | `scripts/label_eval_cases.py` `cases` | 스냅샷, 페르소나 TOML, 소유자 직접 라벨(선택) | `cases.jsonl`, `cases.jsonl.sha256` |
| 실행 | `scripts/label_eval_run.py` | 사례 | `results.jsonl`(추가 기록) |
| 채점 | `scripts/label_eval_score.py` | 사례, 결과, 지연 결과·`prices.toml`·`decision.toml`(선택) | `summary.json`, `report.md` |

## 공통 동작

- 인자나 입력 파일을 거부하면 `rejected: ...`를 stderr에 쓰고 종료 코드 2로 끝나며 출력 파일을 쓰지 않는다. argparse 인자 오류(필수 인자 누락, 범위 밖 값)도 종료 코드 2다.
- 실행 스크립트의 거부는 모두 엔진 호출 전에 일어난다(호출 0회).
- 스크립트는 현재 시각을 읽지 않는다. 시점은 `--as-of` 같은 인자로만 받는다.
- 스냅샷·사례 출력에는 개인 활동이 들어 있다. `snapshot`·`cases`는 `--out`이 git work tree 안이면(이 저장소의 다른 checkout 포함) 종료 코드 2로 거부한다. 출력은 소유자만 읽을 수 있는 권한(0600)으로 쓴다. 실행·채점 출력도 같은 디렉터리에 두고 커밋하지 않는다.

## 사용법

예시는 평가 디렉터리를 `EVAL=~/.mosemo/label-eval/2026-11-22`처럼 저장소 밖에 잡는다고 가정한다.

```sh
mkdir -p "$EVAL"
uv run --locked --env-file .env python scripts/label_eval_cases.py snapshot \
  --account-id "$ACCOUNT_ID" --from 2026-11-01T00:00:00Z --to 2026-11-22T00:00:00Z \
  --as-of 2026-11-22T00:00:00Z --out "$EVAL/activities.jsonl"

uv run --locked python scripts/label_eval_cases.py cases \
  --activities "$EVAL/activities.jsonl" \
  --persona "$EVAL/personas/study.toml" --persona "$EVAL/personas/work.toml" \
  --out "$EVAL/cases.jsonl"

uv run --locked --env-file .env python scripts/label_eval_run.py \
  --cases "$EVAL/cases.jsonl" --engine a --min-interval-ms 4000 \
  --max-requests 18000 --out "$EVAL/results.jsonl"
uv run --locked --env-file .env python scripts/label_eval_run.py \
  --cases "$EVAL/cases.jsonl" --engine c --c-route openrouter \
  --max-requests 2000 --out "$EVAL/results.jsonl"

uv run --locked python scripts/label_eval_score.py \
  --cases "$EVAL/cases.jsonl" --results "$EVAL/results.jsonl" \
  --prices "$EVAL/prices.toml" --decision "$EVAL/decision.toml" --out "$EVAL/score"
```

실행 스크립트는 키를 환경 변수로만 읽으므로 키가 `.env`에 있으면 `--env-file`을 준다. 예시의 `--max-requests`는 사례 2,000건(활동 500개 × 페르소나 4개)을 `LABEL_MODEL=openai:...`의 A(요청당 9)와 C(요청당 1)로 한 번씩 부르는 값이고, 실제 값은 「운영 절차」 6의 식으로 계산한다.

명령마다 `--help`로 같은 옵션 목록을 볼 수 있다. 아래 표는 `--help`를 뺀 옵션 전부다(테스트가 `--help` 출력과 같은지 검사한다).

### cases snapshot

`label_eval_cases.py snapshot`은 `read_snapshot` 트랜잭션 하나에서 `--as-of` 시점으로 본 타임라인을 읽고, 닫힌 detailed 구간(`CLOSED_DETAILED_ACTIVITY`) 중 `--from ≤ ended_at < --to`인 것을 `(ended_at, activity_id)` 순으로 쓴다. DB 연결은 `scripts/seed_load.py`와 같은 `DB_*` 환경 변수를 쓴다. 같은 DB·같은 인자면 같은 sha256이 나온다.

| 옵션 | 필수 | 의미 |
| --- | --- | --- |
| `--account-id` | 예 | 대상 계정 UUID. |
| `--from` | 예 | 구간 `ended_at` 하한(포함). timezone 필수인 ISO 8601. |
| `--to` | 예 | 구간 `ended_at` 상한(제외). timezone 필수. `--as-of`보다 늦으면 종료 코드 2. |
| `--as-of` | 예 | 타임라인을 보는 시점. 이 시점에 진행 중인 구간은 빠진다. timezone 필수. |
| `--out` | 예 | `activities.jsonl` 경로. `{경로}.sha256`도 함께 쓴다. git work tree 안이면 거부. |

### cases cases

`label_eval_cases.py cases`는 페르소나 규칙으로 정답을 정하고, 사례마다 그 시점에 있었을 few-shot 예시를 다시 만든다.

| 옵션 | 필수 | 의미 |
| --- | --- | --- |
| `--activities` | 예 | 스냅샷 `activities.jsonl`. `activity_id`가 중복되면 거부. |
| `--persona` | 예 | 페르소나 TOML. 여러 번 줄 수 있다. 이름은 서로 달라야 하고 `owner`일 수 없다. |
| `--owner-labels` | 아니요 | 소유자 직접 라벨 JSONL. `--owner-label-names`와 함께만 쓴다. |
| `--owner-label-names` | 아니요 | 소유자 라벨 이름을 담은 TOML(`labels`만, `rules` 없음). `--owner-labels`와 함께만 쓴다. |
| `--out` | 예 | `cases.jsonl` 경로. `{경로}.sha256`도 함께 쓴다. git work tree 안이면 거부. |

### run

`label_eval_run.py`는 사례를 엔진 하나에 넣고 사례마다 결과 행 하나를 `--out`에 덧붙인다. 호출은 순차 실행이다.

| 옵션 | 필수 | 의미 |
| --- | --- | --- |
| `--cases` | 예 | `cases.jsonl`. |
| `--engine` | 예 | `a`, `a-batch`, `b`, `b-none`, `c`, `c-none`, `oracle`, `random` 중 하나(아래 엔진 표). |
| `--c-route` | `c`·`c-none`이면 예 | C 경로 `aimlapi`, `openrouter`, `ollama`. `c`·`c-none`에만 쓰고, 다른 엔진에 주면 종료 코드 2로 거부한다. 결과 행의 `engine`은 `c@{경로}`·`c-none@{경로}`다. |
| `--batch-size` | 아니요 | 한 호출에 묶는 사례 수, 1~50, 기본 1. `--engine a`는 한 호출에 한 건만 받으므로 1이 아니면 종료 코드 2로 거부한다. 묶음은 이번 실행의 대상 사례를 페르소나별 스냅샷 순서로 N개씩 자르고, 첫 사례의 예시를 공유한다. |
| `--repeats` | 아니요 | 반복 횟수, 1 이상, 기본 1. 결과 행의 `rep`은 0부터다. |
| `--sample` | 아니요 | `sha256("{seed}/{case_id}")` 오름차순 상위 N개만 쓴다. 엔진과 무관하게 같은 사례를 고른다. |
| `--seed` | 아니요 | 표본과 `random` 엔진의 시드, 기본 0. |
| `--out` | 예 | `results.jsonl`. 있으면 읽어 재개하고, 새 행을 덧붙인다. |
| `--max-requests` | `oracle`·`random` 밖이면 예 | 실제 요청 예산. `남은 논리 호출 수 × 요청당 최대 실제 시도`가 이 값을 넘으면 호출 0회로 종료 코드 2. `c@ollama`도 필수다. |
| `--min-interval-ms` | 아니요 | 직전 호출 시작부터 이 시간(ms)이 지나야 다음 호출을 보낸다. 기본 0. 대기는 지연에 포함하지 않는다. |

엔진:

| 엔진 | 요청 | 답 | 요청당 최대 실제 시도 | 키 환경 변수 |
| --- | --- | --- | --- | --- |
| `a` | 운영 `PydanticAILabelSuggester`에 사례 1건 | `label_id` 또는 null, confidence 없음 | `openai:` 9, `google:` 3 | `LABEL_MODEL`의 공급자 기본 키(`OPENAI_API_KEY`, `GOOGLE_API_KEY`) |
| `a-batch` | PydanticAI `Agent` 1회에 묶음 N건 | 같음 | 같음 | 같음 |
| `b` | `POST https://api.openai.com/v1/decisions`, `gpt-6-luna`, 사례마다 `choice` 질문 1개 | `choice`·`confidence`·`probabilities`, `refusal`이면 null과 `refused: true` | 1 | `OPENAI_API_KEY` |
| `b-none` | `b`에 `none`(근거 부족) 선택지 추가 | `none`이면 null | 1 | `OPENAI_API_KEY` |
| `c` | `--c-route`의 경로로 `state` + `questions` | `b`와 같음 | 1 | 경로 표 |
| `c-none` | `c`의 `criteria`에 `none` 추가 | `none`이면 null | 1 | 경로 표 |
| `oracle` | 호출 없음 | 정답 그대로, confidence 1.0 | 0 | 없음 |
| `random` | 호출 없음 | 라벨들과 null 중 균등, confidence null | 0 | 없음 |

- A 계열은 `LABEL_MODEL`의 접두사(`openai:`, `google:`)로 모델을 만들고, 다른 접두사는 거부한다. 최대 시도는 운영 출력 검증 시도 3 × 공급자 SDK 기본 HTTP 시도(`openai:` 3, `google:` 1)다. 운영 어댑터를 그대로 쓰므로 SDK 재시도를 끄지 않는다.
- B·C는 `httpx2`로 직접 한 번만 보낸다. 타임아웃은 30초다.
- B·C 키(`OPENAI_API_KEY`, `AIMLAPI_API_KEY`, `OPENROUTER_API_KEY`)가 비었거나, 인쇄 가능한 ASCII가 아니거나, 앞뒤 공백이 있으면 호출 전에 종료 코드 2로 거부한다. 메시지에는 변수 이름만 나온다. 키 값은 결과·로그·오류 메시지에 남지 않는다.

C 경로:

| 경로 | URL | `model` | 키 환경 변수 | `cost_usd` |
| --- | --- | --- | --- | --- |
| `aimlapi` | `https://api.aimlapi.com/v1/decisions` | `typesafe/jev` | `AIMLAPI_API_KEY` | 응답 `meta.usage.usd_spent`가 있으면 그 값 |
| `openrouter` | `https://openrouter.ai/api/v1/systemone` | `typesafe/jev-1.13` | `OPENROUTER_API_KEY` | 응답 `usage.cost` |
| `ollama` | `http://localhost:11434/v1/systemone` | `laya` | 없음(키를 읽지 않고 `Authorization` 헤더도 보내지 않음) | null |

- C의 세 경로는 답 형식을 공유한다. `confidence`·`probabilities`가 없는 답도 오류 없이 받고 그 값은 null로 남긴다(OpenRouter 문서는 둘을 선택 필드로 둔다).
- `c@openrouter`의 `cost_usd`는 응답 `usage.cost`이고, OpenRouter 문서는 이 값을 credits 단위(BYOK 요청이면 OpenRouter 수수료만)로 설명한다. 가격 시나리오를 해석할 때 이 단위를 함께 적는다.
- `ollama`는 시운전 전용 경로다(「시운전」, 「한계」).

재개: 키 `(case_id, engine, batch_size, rep)`에 오류 없는 행이 이미 있으면 부르지 않는다. 오류 행만 있으면 다시 부르고 새 행을 덧붙인다. 채점은 키마다 마지막 행을 쓴다.

### score

`label_eval_score.py`는 엔진·묶음 크기·페르소나별 지표, 기준 `(a, batch_size 1, rep 0)`과의 같은 coverage 비교, `--decision`이 있으면 판정과 최종 결론을 낸다. 같은 입력이면 같은 바이트다.

| 옵션 | 필수 | 의미 |
| --- | --- | --- |
| `--cases` | 예 | `cases.jsonl`. |
| `--results` | 예 | `results.jsonl`. 사례에 없는 `case_id`의 행이 있으면 거부. |
| `--latency-results` | 아니요 | 랩에서 따로 잰 결과 파일. 있으면 판정의 p95 지연을 이 파일의 해당 엔진(batch 1) 호출에서 계산하고, 그 엔진 호출이 없으면 p95는 null이다(`--results`로 대체하지 않음). |
| `--prices` | 아니요 | `prices.toml`. 가격표에 없는 엔진(파일을 주지 않은 경우 포함)은 응답이 준 `cost_usd`만 쓰고, 그 값이 없는 호출의 비용은 null이다. |
| `--decision` | 아니요 | `decision.toml`. 기준 A 행(batch 1, rep 0)이 없거나 `primary` 엔진의 batch 1·rep 0 행이 없으면(맨손 `c`에 맞는 `c@*`가 없는 경우 포함) 종료 코드 2. |
| `--seed` | 아니요 | bootstrap 시드, 기본 0. |
| `--out` | 예 | 출력 디렉터리. 없으면 만들고 `summary.json`·`report.md`를 쓴다. |

- 기준 A 행이 하나도 없으면 비교 목록은 빈 목록이고 엔진별 지표만 나온다.
- 판정은 페르소나 사례만 쓴다. owner 사례와 `primary` 밖 비교(`-none`, 묶음 변형)는 지표·CI만 보고한다.
- 지표·비교·판정의 정확한 정의는 #309 「API 계약·데이터·트랜잭션·동시성」의 「채점」이 원본이다.

## 파일 형식

JSONL 파일은 UTF-8, 한 줄에 JSON 객체 하나, 키 정렬 직렬화다. 시각은 timezone이 붙은 ISO 8601이다.

- 스냅샷 행: `{"activity_id", "account_id", "version", "ended_at", "activity", "fields"}`. `activity_id`는 구간의 `first_event_id`, `activity`는 운영 `summarize_context` 결과(엔진 입력), `fields`는 같은 규칙(값 256자 절단, URL은 host만)으로 뽑은 `app_bundle_id`·`app_name`·`window_title`·`tab_title`·`web_host`(없으면 null)다. `.sha256`은 `sha256sum` 형식이다.
- 페르소나 TOML: `name`, `labels`(2~254개, 중복·`"null"`·`"none"` 금지), `[[rules]]`. 규칙 조건은 `app_bundle_id`·`app_name`(정확히 일치 목록), `web_host_suffix`(host 자신 또는 그 하위 도메인과 맞는 도메인 목록, 대소문자 무시), `title_contains_any`(`window_title` 또는 `tab_title` 부분 문자열, 대소문자 무시)이고, 규칙 안은 AND, 목록 안은 OR이다. 위에서부터 처음 맞는 규칙의 `label`(`labels`의 이름 또는 기권 정답 `"null"`)이 정답이고, 맞는 규칙이 없으면 정답은 null이다. 알 수 없는 키, `labels`에 없는 라벨, 조건 없는 규칙은 거부한다. 형식 예시는 `scripts/label_eval_personas/example.toml`이다. 실제 평가용 페르소나는 저장소 밖에 둔다.
- 소유자 직접 라벨: `--owner-labels`는 `{"activity_id", "label"}` JSONL(`label`은 라벨 이름 또는 `"null"`), `--owner-label-names`는 `labels`만 있는 TOML이다. 페르소나 `owner`로 라벨이 붙은 활동만 사례가 된다.
- 사례 행: `{"case_id": "{persona}/{activity_id}", "persona", "activity_id", "activity", "fields", "labels": [{"label_id", "name"}], "examples": [{"activity_id", "activity", "label_id"}], "gold", "tags"}`. 라벨 id는 `uuid5(NAMESPACE_URL, "mosemo-label-eval/{persona}/{label}")`다. `examples`는 같은 페르소나에서 스냅샷 순서상 엄격히 앞선 사례 중 최근 최대 8개, 최신순이며 정답 null 사례도 들어간다. `tags`는 `gold_null`, `cold_start`(예시 3개 미만), `new_host`(예시에 없는 `web_host`), `personas_disagree`(같은 활동의 페르소나 정답이 다름, owner 제외)다.
- 결과 행: `{"case_id", "engine", "batch_size", "rep", "call_id", "pred", "confidence", "prob", "refused", "latency_ms", "input_tokens", "output_tokens", "cost_usd", "attempts", "error"}`.
  - `pred`는 라벨 id 또는 null, `prob`는 고른 답의 확률(B는 `probabilities`에서 고른 `choice`의 값, C는 `probabilities[choice]`)이다. 응답에 없거나 `refusal`·오류이거나 A 계열·`oracle`·`random`이면 null이다.
  - 묶음 호출의 행은 같은 `call_id`를 공유하고, 지연·토큰·`cost_usd`는 채점에서 `call_id`당 한 번만 센다. `latency_ms`는 재시도를 포함한 호출 전체 시간이다.
  - `error`는 예외 타입 이름만 담는다. 오류 행은 정답이 null이어도 오답으로 센다.
  - `attempts`의 뜻은 엔진마다 다르다. `a`는 운영 어댑터가 요청 수를 돌려주지 않아 null, `a-batch`는 PydanticAI `usage.requests`(출력 검증 시도 수, SDK HTTP 재시도 미포함), B·C는 정상 행 1·오류 행 null, `oracle`·`random`은 0이다. 비용 상한(`--max-requests`)은 `attempts`가 아니라 엔진 표의 요청당 최대 실제 시도로 계산한다.
- `prices.toml`: 엔진 이름(결과 행의 `engine`, 예: `a`, `b`, `c@openrouter`)마다 표 하나. 키는 `input_usd_per_mtok`·`output_usd_per_mtok`·`usd_per_request`(없으면 0, 음수 거부)와 `source_url`·`retrieved_on`(출처 기록)뿐이다. 호출에 `cost_usd`가 있으면 그 값을, 없으면 가격표로 계산한다. 토큰이 null인 호출이 있는데 토큰 단가가 0이 아니면 비용은 null이다.

  ```toml
  [a]
  input_usd_per_mtok = 0
  output_usd_per_mtok = 0
  source_url = "https://ai.google.dev/gemini-api/docs/pricing"
  retrieved_on = 2026-11-22
  ```

- `decision.toml`: 네 키가 정확히 있어야 한다. 아래 숫자는 형식 예시이고, 실제 값은 실행 전에 소유자가 정한다.

  ```toml
  primary = ["b", "c"]          # 주 비교 대상(batch 1). 맨손 c는 결과의 유일한 c@*로 푼다
  min_accuracy_diff = 0.0       # 음수면 비열등 기준
  max_p95_latency_ms = 1000
  max_usd_per_correct = 0.001
  ```

  `c@*`가 둘 이상이면 맨손 `c`는 거부되므로 `c@openrouter`처럼 경로까지 적는다. 신뢰 수준은 `1 − 0.05 / len(primary)`(Bonferroni)다.
- `summary.json`·`report.md`: 엔진별 지표(coverage, `selective_accuracy`, 확신 오답률, null 적중률, `exact_match`, 오류율, 거절률, 문턱 곡선, ECE, 페르소나 추종률, 지연 p50/p95/p99, 반복 일치율, 비용, 태그별 분해), 같은 coverage 비교(McNemar, bootstrap CI), 판정과 최종 결론(`A 유지`, B, `C(경로)`, `판단 불가`)이다.

## 운영 절차

실제 평가는 소유자가 아래 순서로 진행한다. 각 단계는 #309 「마이그레이션·배포·롤백」의 운영 절차가 원본이다.

1. 클라이언트를 로컬 서버에 붙여 사용하며 활동을 쌓는다. 목표는 닫힌 detailed 구간 약 500개다.
2. `snapshot --as-of`로 고정한다. 출력은 저장소 밖(`~/.mosemo/label-eval/{날짜}/`)에 두고 커밋하지 않는다.
3. 엔진 실행 전에 페르소나 3~4개, `decision.toml`(소유자가 정한 값), `prices.toml`을 확정하고 `sha256sum`으로 해시를 남긴다.
4. 모든 엔진(A의 Gemini 무료 등급 포함)의 데이터 이용 조건(학습 사용·보존)을 같은 기준으로 확인하고, 실제 데이터 외부 전송을 결정한다. C는 중계를 거치므로 데이터가 업체 두 곳을 지난다.
5. 엔진마다 사례 1건(`--sample 1`)으로 실제 응답 형식을 확인한다. 문서와 다르면 실행 전에 어댑터를 고친다.
6. 실행한다.
   - A는 무료 등급 요청 한도(429)에 맞춰 `--min-interval-ms`를 준다.
   - 첫 실행 뒤 오류 행만 다시 부르는 재개 실행(같은 명령 재실행)은 모든 엔진에 최대 3회이고, 그 뒤 남은 오류는 오답으로 센다. 보고서에 엔진별 오류율을 함께 적는다.
   - `--max-requests`는 (사례 수 + 반복 표본 × (반복 − 1) + 묶음 호출 수) × 요청당 최대 실제 시도로 잡는다. 재개 실행마다 남은 호출 기준으로 다시 검사되므로 예산은 첫 실행 기준 최대 4배다.
7. 채점한다. A 가격을 무료 등급 0원과 유료 단가 두 `prices.toml`로 각각 채점해 보고한다. 판정의 비용 상한은 B·C에만 걸리므로 판정은 같다.
8. 지연은 랩(AWS) 리전에서 엔진당 약 200회를 따로 실행해 재고, 그 결과 파일을 `--latency-results`로 넘긴다. 랩 기동은 소유자 허락 후에만 한다.
9. 소유자 직접 라벨(약 150건, 며칠 뒤 50건 재라벨링)은 선택이다. 하지 않으면 보고서에 "현실 성능 미측정"을 적는다.

## 시운전

키 없이 사례 생성 → 실행 → 채점 흐름만 확인하는 선택 절차다. 위 운영 절차와 별개이고, 로컬 실행이라 외부 전송이 없다.

1. WSL 안에서 Ollama 0.40.0 이상을 띄우고 `ollama pull laya`로 모델을 받는다. 주소는 `localhost:11434`로 고정이다.
2. 데이터는 `scripts/seed_load.py`로 채운 로컬 DB의 스냅샷(「[부하 테스트 시드 적재](load-test-seeding.md)」) 또는 쌓인 실제 기록의 스냅샷을 쓴다. 페르소나는 `scripts/label_eval_personas/example.toml`을 쓴다.
3. 결과는 실제 평가와 다른 디렉터리(`~/.mosemo/label-eval/trial-{날짜}/`, 커밋 금지)에 두고, `ollama --version` 출력과 `curl -s http://localhost:11434/api/tags`의 `laya` digest를 같은 디렉터리에 남긴다.
4. `--batch-size`는 1부터 시작한다.

   ```sh
   TRIAL=~/.mosemo/label-eval/trial-2026-11-22
   uv run --locked python scripts/label_eval_cases.py cases \
     --activities "$TRIAL/activities.jsonl" \
     --persona scripts/label_eval_personas/example.toml --out "$TRIAL/cases.jsonl"
   for engine in oracle random; do
     uv run --locked python scripts/label_eval_run.py \
       --cases "$TRIAL/cases.jsonl" --engine "$engine" --out "$TRIAL/results.jsonl"
   done
   uv run --locked python scripts/label_eval_run.py \
     --cases "$TRIAL/cases.jsonl" --engine c --c-route ollama \
     --max-requests 1000 --out "$TRIAL/results.jsonl"
   uv run --locked python scripts/label_eval_score.py \
     --cases "$TRIAL/cases.jsonl" --results "$TRIAL/results.jsonl" --out "$TRIAL/score"
   ```

5. 기준 A 행이 없으므로 `--decision` 없이 채점한다. 이 결과의 정확도·ECE는 인용하지 않는다.

## 한계

- 페르소나는 실제 사람보다 일관적이라 결과는 상한이다.
- 평가는 앞선 모든 구간이 끝나는 즉시 확정됐다고 가정하고 `ended_at` 순으로 예시를 고른다. 운영은 일부만 확정되고 확정 `updated_at` 순이다.
- 같은 활동이 페르소나마다 사례가 되어 사례끼리 독립이 아니다. bootstrap은 `activity_id` 단위로 재표집한다. McNemar는 사례 단위라 p값이 낙관적일 수 있어 보고용이고, 판정은 CI·지연·비용으로만 한다.
- ECE는 `prob`로 계산한다. `confidence`는 확률 집중도일 수 있어 ECE에 쓰지 않는다.
- `c@ollama`(`laya`)는 하네스 흐름 확인용이고 정확도·ECE·판정에 쓰지 않는다. 기본 태그는 영어 전용인데 라벨·창 제목은 한글이다. 질문마다 지시문·선택지·`state`를 합쳐 512토큰 예산이고 넘는 `state`는 오류 없이 잘린다. 선택지 2~26개, 요청당 질문 64개, 본문 64KiB를 넘는 요청은 HTTP 오류 행이 된다.
