---
status: draft
---
# 활동 분석 운영

일간 통계 배치(`analytics-daily-stats`)와 정의 재처리 표시(`analytics-definition-mark`)를 운영하는 방법이다.
프로토콜과 수치의 근거는 [ADR 0016](adr/0016-compute-activity-analytics-on-read-and-store-daily-stats.md)
「일간 통계 프로토콜」·「실패·재처리」에 있다. 배치는 지난 날짜를 미리 채우는 캐시이며, 배치가 멈추거나
늦어도 조회는 원천에서 계산해 정확한 값을 돌려준다(느려질 뿐이다).

## 프로세스 배치

- 일간 통계 배치와 정의 재처리는 batch 프로세스(`python -m mosemo.entrypoints.batch`)에서만 돌린다.
  `serve`가 `analytics-daily-stats`를 5분마다(시작 즉시 1회) 실행하고, `analytics-definition-mark`는 스케줄이
  없어 `python -m mosemo.entrypoints.batch run analytics-definition-mark`로 손으로 실행한다.
- API·consumer Pod에서 돌리지 않는다. 5단계 측정에서 재처리가 조회와 같은 CPU를 쓰면 조회 p95가 62ms에서
  2~4초로 늘었다.
- 실행 한 번의 시간 예산은 120초이며 날짜 사이에서만 확인한다. 한 번에 표시를 최대 50개 선점하고, 다 처리하면
  다시 선점한다. 같은 표시는 한 실행에서 한 번만 처리한다(실행 시작 시각 기준). 예산이 끝나 처리하지 못한
  선점 날짜는 시도 횟수를 되돌리고 바로 다음 실행의 대상이 된다(로그 `released`). 그래서 예산 초과가 정상 날짜를 격리하지 않는다.

## Kafka 파티션 규칙

- consumer Pod 하나는 consumer 그룹 멤버 2개다(`src/mosemo/platform/messaging/subscriber.py`의 `max_workers=2`).
- 토픽 파티션 수를 consumer Pod 수 × 2 이상으로 둔다. 파티션이 멤버 수보다 적으면 남는 멤버가 논다.
- 프로필별 현재 값(Pod 수·파티션 수)과 렌더 방법은 [배포 문서](../infra/deployment/README.md)를 본다. 이 문서에는
  숫자를 따로 적지 않는다.

## 설정

| 환경 변수 | 기본값 | 뜻 |
| --- | --- | --- |
| `ANALYTICS_REPROCESS_PER_RUN` | 600 | 실행 한 번에 선점할 정의 재처리 표시 상한. 0이면 정의 재처리를 멈춘다(원천 표시는 계속 처리). 음수는 시작 때 거절한다. 바꾸면 batch 프로세스를 재시작한다. |

`ANALYTICS_TEST_WAIT_POINTS`·`ANALYTICS_TEST_WAIT_DIRECTORY`는 테스트 전용 대기 지점이며 운영 설정에 넣지 않는다.

## 정의 변경 절차

정의(`CURRENT_DEFINITION`)나 동봉한 PSL 스냅샷(`public_suffix_list.dat`)을 바꿀 때 같은 절차를 따른다.

1. 정의 상수의 version을 올리는 변경과 `analytics_definitions.version`을 같은 값으로 올리는 Alembic 마이그레이션을
   한 커밋에 넣는다. 정의 행은 줄이지 않는다. 되돌리기는 옛 로직을 새 version으로 배포하는 것이다. downgrade
   마이그레이션은 새 정의로 저장된 행이 하나라도 있으면 실패해야 한다.
2. 배포한다. 옛 코드 배치는 실행 시작에서 정의 불일치(`definition_mismatch`)로 선점 없이 끝나고, 저장 트랜잭션
   안에서 정의가 바뀌면 롤백한다(`aborted=definition`).
3. 옛 batch 프로세스가 남아 있지 않음을 확인한다.
4. mark를 실행한다: `python -m mosemo.entrypoints.batch run analytics-definition-mark`. 반드시 정의 행을 올린 뒤에
   한다. 먼저 하면 옛 코드 배치가 표시를 옛 정의로 처리해 지운다. mark는 계정 id 순서로 계정마다 계정 잠금 안에서
   표시를 쓰며, 중단돼도 다시 실행하면 된다(이미 새 정의로 저장된 날짜는 빠진다). 로그의 `accounts`·`markers`가
   처리한 계정 수와 표시 행 수다.
5. `ANALYTICS_REPROCESS_PER_RUN`으로 속도를 조절한다(0이면 멈춤, 바꾸면 재시작).
6. 아래 완료 확인 SQL이 모두 0이면 끝이다(`:v`는 새 정의 version).

```sql
-- 1. 표시 없는 옛 정의 저장 행
SELECT count(*) FROM analytics_daily_stats s
WHERE s.definition_version < :v
  AND NOT EXISTS (SELECT 1 FROM analytics_daily_markers m
                  WHERE m.account_id = s.account_id AND m.local_date = s.local_date);
-- 2. 남은 정의 재처리 표시(격리 제외)
SELECT count(*) FROM analytics_daily_markers WHERE kind = 'definition' AND attempts < 6;
-- 3. 격리 날짜가 아닌 옛 정의 저장 행
SELECT count(*) FROM analytics_daily_stats s
WHERE s.definition_version < :v
  AND NOT EXISTS (SELECT 1 FROM analytics_daily_markers m
                  WHERE m.account_id = s.account_id AND m.local_date = s.local_date
                    AND m.attempts >= 6);
-- 4. 정의 역전(정의 행보다 큰 정의로 저장된 행)
SELECT count(*) FROM analytics_daily_stats
WHERE definition_version > (SELECT version FROM analytics_definitions WHERE id = 1);
```

## 격리 해제

계산이 같은 날짜에서 반복 실패하거나 처리 중 프로세스가 반복 종료되면 시도 6회에서 그 표시가 선점에서 빠진다
(격리). 표시가 남아 있으므로 그 날짜의 조회는 원천에서 계산하고, 계산 자체가 예외를 내면 그 날짜를 포함한 조회가
공통 오류로 실패한다. 해제 전에 원인을 고친다: `last_error`(예외 타입 이름)와 실패 로그로 원인을 찾고, 계산 결함이면
수정을 배포한 뒤 해제한다. 해제하지 않은 격리 표시는 저장 없이 사라지지 않는다.

```sql
UPDATE analytics_daily_markers
SET attempts = 0, next_attempt_at = now(), last_error = NULL, last_failed_at = NULL
WHERE attempts >= 6;  -- 필요하면 account_id·local_date 조건을 더한다
```

## 운영 지표

| 지표 | 읽는 곳 |
| --- | --- |
| 가장 오래된 원천 표시의 나이 | `SELECT now() - min(eligible_at) FROM analytics_daily_markers WHERE kind = 'source' AND attempts < 6 AND eligible_at <= now()` |
| 격리 수 | `SELECT count(*) FROM analytics_daily_markers WHERE attempts >= 6` |
| 마지막 성공 실행 시각 | `operation = analytics-daily-stats`, `outcome = success`인 마지막 로그의 `timestamp` |
| 조회 중 원천 계산 비율 | `analytics read` 로그의 `source = computed` 비율 |
| 원천 쓰기 `lock_timeout` 수 | 원천 쓰기의 `ACTIVITY_TIMELINE_BUSY` 오류 응답 수 |
| 실행 로그 카운터 | job 결과 로그의 `saved`, `superseded`(저장했지만 계산 중 원천이 바뀌어 표시가 남음), `stale`(더 새 저장 행이 있어 무시), `failed`와 분류별 `failed_*`, `quarantined_new`, `released`(예산 초과로 되돌린 선점), `definition_mismatch`, `aborted`, `max_source_read_ms`, `max_compute_ms` ([로깅 규칙](logging.md)) |

연결 오류로 끝난 실행은 job 실패로 기록되고(날짜 처리 중이면 `aborted=connection`, 시작 확인·선점 중이면 카운터 없이 실패 로그만), 선점한 날짜는 lease(10분) 뒤 다시 처리된다.

## 보류

- 재처리 속도 기본값(`ANALYTICS_REPROCESS_PER_RUN` 600)과 표본 검증 방법은 측정 전 가정이다. 첫 정의 변경 전에
  측정으로 정한다.

## 관련 문서

- [ADR 0016 조회 때 계산과 일간 통계 저장](adr/0016-compute-activity-analytics-on-read-and-store-daily-stats.md)
- [활동 분석 검증 계획](activity-analytics-verification.md)
- [로깅 규칙](logging.md)
