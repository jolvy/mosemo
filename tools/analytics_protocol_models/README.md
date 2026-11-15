# 일간 통계 프로토콜 상태 모델

ADR 0016 「일간 통계 프로토콜」·「변경 표시」의 전수 탐색 모델이다. 각 모델은 원자 단계(PostgreSQL의 한 문장
또는 한 커밋)를 전이로 두고 도달 가능한 모든 상태를 BFS로 방문해 불변식(I1~I14, 각 모듈 docstring)을
검사한다. 추천 설계 변형과 대조 변형을 함께 탐색하고, 변형별 기대 결과(위반 0 또는 위반한 불변식 집합)를
모듈의 `EXPECTED` 상수로 가진다. 표준 라이브러리만 쓴다.

실행은 저장소 루트에서 `python -m tools.analytics_protocol_models.<모듈> [변형…] [--trace] [--small]`이다.
`--trace`는 위반 종류마다 가장 짧은 반례를 출력하고, `--small`은 CI용 작은 시나리오를 돌린다. 결과가
`EXPECTED`와 다른 변형이 있으면 종료 코드 1로 끝난다.

## 모델과 전체 탐색

ADR 0016 프로토콜(표시·배치·저장 guard·조회 스냅샷·재시도·시간대 변경·90일 보관·세션 표시)을 바꿀 때 수동으로
전체 탐색을 돌린다. 전체 탐색은 모델마다 수 분~10분, 메모리 1GB 안팎이라 CI에서 돌리지 않는다.

| 모듈 | 대상 | 추천 변형(위반 0) | 전체 실행 명령 |
| --- | --- | --- | --- |
| `protocol_model` | 표시·배치·저장 guard·정의 확인·조회 스냅샷(4단계 D, SM-1) | V0, V0s, V0r, V5, V8, V11 | `uv run --locked python -m tools.analytics_protocol_models.protocol_model` |
| `protocol_model_4b` | 재시도·lease·격리 열(4B F-1, SM-2) | F5(N1·N4도 위반 0이 기대값) | `uv run --locked python -m tools.analytics_protocol_models.protocol_model_4b` |
| `protocol_model_tz` | 계정 시간대 변경 1차, 날짜 2개(F-2, SM-3) | Z1, Z1x, Z9u | `uv run --locked python -m tools.analytics_protocol_models.protocol_model_tz` |
| `protocol_model_tz3` | 시간대 변경 표시 대상, 날짜 3개(F-2 3차, SM-3) | Ax, A2 | `uv run --locked python -m tools.analytics_protocol_models.protocol_model_tz3` |
| `protocol_model_tz3_distinct` | tz3 A0의 표시 대상과 "모든 날짜"의 차이 분류(보조, 기대 결과 없음) | — | `uv run --locked python -m tools.analytics_protocol_models.protocol_model_tz3_distinct` |
| `protocol_model_archive` | 90일 아카이브·S3 재계산·동결·계정 삭제(F-3, 7B 범위) | R3, R3f | `uv run --locked python -m tools.analytics_protocol_models.protocol_model_archive` |
| `session_marks` | 일간 행 세션 합의 변경 표시, 결정 D1(F-4, SM-5) | S0 | `uv run --locked python -m tools.analytics_protocol_models.session_marks` |

대조 변형의 기대 결과는 각 모듈의 `EXPECTED`에 있다. 옮기기 전 원본의 실행 출력에 없던 변형(tz의 Z4l,
archive의 R3·R3f 밖 변형)은 전체 탐색 기대값이 없어 비교하지 않는다(요약에 "이 시나리오의 기대 결과 없음"과
"기대 결과가 없어 비교하지 않은 변형: …"을 출력한다). archive의
대조 변형은 작은 시나리오 결과(X1)를 `SMALL_EXPECTED`로 둔다.
작은 시나리오의 기대 결과는 전체 탐색과 따로 `SMALL_EXPECTED`에 둔다. `protocol_model_4b`의 기대 결과는 기본 예산(실패 1·강제
종료 1)의 값이며 `--fails`·`--crashes`로 예산을 바꾸면 비교하지 않는다. `protocol_model_archive`에 시나리오
이름(`S1`…)을 주면 그 시나리오만 돌리고 기대 결과와 비교하지 않는다.

ADR 0016 「시간대 변경」은 표시 대상을 "저장 행이 있는 날짜와 표시가 있는 날짜, 그리고 각각의 앞뒤 하루"로
정했다. tz3의 A0(같은 규칙)가 I8을 보고하는 경우는 시차 24시간 초과 배치 L3E에서 `protocol_model_tz3_distinct`가
분류한 "빠진 날짜에 새 tz 활동 있음"(저장 행·표시가 없는 날짜)이며, 그런 날짜는 조회가 원천에서 계산한다.
그래서 README 기준으로 A0는 대조 변형이다.

## CI

`tests/unit/tools/test_analytics_protocol_models.py`가 모델마다 `--small`(추천 변형 하나와 대조 변형 하나, 범위를
줄인 시나리오)을 하위 프로세스로 돌려 종료 코드와 마지막 요약을 확인한다. `session_marks`는 전체 탐색이 1초 안이라
CI에서 그대로 돌린다. #285(세션 표시 handler)는 `session_marks`의 S0 통과를 병합 조건으로 둔다.

## 이식 기록(#280)

원본은 f1c3ca9의 `tools/analytics_originals/protocol_models/*.py.txt`다. 이식에서 바꾼 것은 이름·타입·구조뿐이다.
상태·전이·불변식 검사·변형과 시나리오 정의는 그대로다.

| 모듈 | 원본 대비 변경 |
| --- | --- |
| 모든 모듈 | 세 번째 이후 인자를 키워드 전용으로 바꾸고 호출에 키워드를 붙임. 타입 주석. 같은 값의 이름 상수(`NG`, `SOLO_RUNS`, `NEW_DEF` 등). `Result`의 dict 기본값을 `field(default_factory=dict)`로. 쓰지 않던 인자(`v`) 제거. `time.time` → `time.perf_counter`(소요 시간 출력만). 배치 반복 안의 `put`이 반복 변수를 기본값으로 묶음(같은 반복에서만 호출). `run`·`main`에 `EXPECTED`·`SMALL_EXPECTED` 비교와 종료 코드·`--small` 추가 |
| `protocol_model` | `G` → `copy_globals`. 실행 순서 수 계산을 `count_orders`로 분리. `is_terminal`을 `all(... for p in s[NG:])`로(프로세스 5개 모두 같은 검사). 쓰지 않던 `lru_cache` import 제거 |
| `protocol_model_4b` | 배치 반복 본문을 `batch_successors`로, 실행 순서 수 계산을 `count_orders`로 분리(같은 코드). I6·I6g 판정의 중첩 `if`를 `and` 하나로. 기대 결과 비교용 `comparable` 추가(I6과 함께 나온 I6g만 I6에 포함, 출력 판정은 그대로) |
| `protocol_model_tz`, `protocol_model_tz3` | `G` → `copy_globals`, `P` → `with_proc`. tz3 docstring의 실행 예시 변형 이름을 tz3의 것(`A0 As`)으로 |
| `protocol_model_archive` | `R` → `reads_of`, `G`·`P` 이름 변경. `copy_parts`의 `conf`·`seg` 두 불리언을 `kinds`(`both`·`seg`·`conf`) 하나로(세 호출 지점이 같은 값으로 대응). 항상 참이던 `shift != "none" or True` 조건 제거. 겹친 `if` 합침 |
| `protocol_model_tz3_distinct` | 패키지 경로 import, `--small`(tz3 작은 시나리오) |

동등성: 원본과 이식본으로 모든 변형을 이식본의 `SMALL_SCENARIOS`에서 돌렸다. 상태·전이·끝 상태·실행 순서·깊이·추가
배치 수·위반 집계(불변식별 건수)가 모두 같았다(2026-11-15, 이 이식 commit 기준). 4b는 실패 1·강제 종료 1이다.

| 모듈 | 작은 시나리오 | 비교한 변형 | 다른 결과 |
| --- | --- | --- | --- |
| `protocol_model` | 정의변경0·초기 stored·변경 a→b | 22 | 0 |
| `protocol_model_4b` | 정의변경0·stored·a→b | 6 | 0 |
| `protocol_model_tz` | LA·초기 stored·변경 없음 | 19 | 0 |
| `protocol_model_tz3` | L3A·초기 stored·변경 없음 | 8 | 0 |
| `protocol_model_archive` | S5·변경 q·초기표시 d1 | 21 | 0 |
| `protocol_model_tz3_distinct` | L3A·초기 stored·변경 없음 | 출력 비교 | 같음 |

archive의 대조 변형 기대값은 작은 시나리오에서 정했다. X1(늦은 관찰 허용)은 S5에서 `{I1, I12}`를 위반한다.
동결되어 아카이브(arch·s3)된 날짜가 읽는 원천의 변경을 받아들이므로 I12가 깨지고, 그 날짜 조회가 원천과 어긋나 I1도 깨진다.
변형 설명 "늦은 관찰 허용(동결 날짜 원천 변경 거부 안 함)"과 맞는다.
