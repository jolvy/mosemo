---
status: accepted
date: 2026-10-17
---
# 활동 배치는 독립된 Kafka 브로커로 전달하고 발행 ACK 후 수락 응답한다

## 맥락

활동 배치 HTTP 요청은 원본 저장과 타임라인 계산이 끝나기 전에 수락 여부를
반환한다. 전환 전 구현은 제한된 메모리 큐와 Worker, TestKafkaBroker를 통해
FastStream Handler를 실행한다. 따라서 202 이후에도 API 프로세스가 종료되면
처리되지 않은 원본이 사라질 수 있고 실제 Kafka의 발행 실패·재전달·offset을
검증할 수 없다.

활동 배치는 애플리케이션과 독립된 Kafka 브로커를 통해 전달한다. HTTP 수락의 의미, 원본과
타임라인의 DB 원자성, 중복 안전성을 구분해야 한다. 처리하지 못한 배치는 Device별로 보류하지 않고 그 배치만 건너뛴다(「처리 실패 정책」).

선택 기준은 실제 브로커 전달의 검증 가능성, 수락 후 API 종료에 대한 원본 보존,
재전달의 중복 안전성, 환경별 연결 구성과 실패 복구 복잡성이다.

## 결정

### 확정한 방향

2026-10-18 설계 대화에서 아래 메시지·실행 계약을 승인했다. 계획은
[Plan Issue #61](https://github.com/jolvy/mosemo/issues/61)에서 관리하며 구현 완료를 뜻하지 않는다.

- 로컬은 Docker Kafka와 영속 volume을 사용하고, 환경별 연결 설정을 주입한다.
  실제 Kafka를 사용하며 TestBroker는 테스트 대역으로만 둔다. HTTP 수락용 메모리
  큐·발행 Worker를 제거한다. 운영 broker 배포·복제 정책은 별도 범위다.
- 인증·입력·Device 귀속 확인 후 배치 전체를 발행한다. 발행은 `acks=1`을 쓰고
  producer idempotence를 쓰지 않으며, 리더의 ACK 확인 후 `202 {"batchId": "...", "status": "accepted"}`를
  반환한다. DB 처리 완료·원본별 성공·consumer offset commit을 기다리는 응답이 아니다.
- 발행 불가·ACK 제한시간 초과는 기존 503·Retry-After 계약을 따른다. 미확인이
  미발행을 뜻하지 않으므로 같은 batchId·내용 재시도와 DB 중복 방어를 유지한다.
- 별도 Pydantic 메시지 모델을 사용하고 HTTP 입력 검증 규칙을 재사용한다.
  value는 `accountId`, `batchId`, `deviceId`, `records`를 담은 JSON이다.
  Publisher가 인증 결과로 value의 accountId를 채우고 그 값의 UTF-8 UUID 문자열을
  key로 생성한다. accountId header는 사용하지 않으며 Consumer가 key와 value의
  계정 일치를 검증한다. HTTP 요청 모델은 유지하고 원본 bytes 보존·이중 JSON 포장은 요구하지 않는다.
- 토픽은 초기 partition 2개다. 개수는 토픽 초기화 구성에서 주입하고 Publisher는
  번호·개수를 직접 지정하지 않고 기본 key partitioner를 사용한다.
  같은 계정은 같은 partition에 모이며 여러 계정이 partition을 공유할 수 있다.
- partition 내부에서는 배치의 DB commit과 해당 처리 위치의 offset commit을
  마친 뒤 다음 배치를 처리한다. partition 간은 병렬로 최대 두 배치를 처리한다.
  다른 partition의 미처리 위치를 넘어 commit하지 않는다. 보장하는 순서는 Kafka
  기록 순서이며 동시 HTTP 요청 시작 순서가 아니다. Kafka도 트랜잭션 시작에
  계정 advisory lock을 잡는다([ADR 0016](0016-compute-activity-analytics-on-read-and-store-daily-stats.md)). 같은 Topic·Consumer Group·계정 Key 매핑을 유지하고 같은 계정의
  legacy 동시 사용을 금지한다. 경로 전환 전 기존 처리를 종료한다. 트랜잭션과
  계정 Lock의 책임은 ADR 0007과 Plan #74의 승인된 변경을 따른다.
- API와 Consumer는 같은 저장소·업무 코드를 공유하되 별도 진입점·프로세스로 실행한다.
- 유효한 committed offset이 있으면 이어서 처리하고, 없는 경우에만
  `auto_offset_reset=earliest`로 현재 보관된 가장 오래된 위치부터 처리한다.
- 채택 원본·타임라인·receipt를 같은 DB 트랜잭션에서 commit한 뒤 offset을 확정한다.
  두 commit은 원자적이지 않으므로 그 사이 종료·재전달은 receipt로 안전하게 처리한다.
  producer idempotence를 쓰지 않으므로 producer 재전송으로 생긴 중복도 receipt가 막는다.
- Consumer 정상 종료는 새 배치 실행을 멈추고 진행 중인 최대 두 배치의 DB·offset commit을
  최대 30초 기다린 뒤 자원을 정리한다. 전체 backlog를 비우는 종료가 아니며
  FastAPI/Uvicorn의 종료 대기 설정과 구분한다.
- Kafka 발행 확인은 최대 5초 기다리며 기존 HTTP 전체 수락 제한도 유지한다.
  제한시간 내 ACK를 확인하지 못하면 기존 503 계약을 적용한다.
- Publisher에 별도 동시 발행 수 제한이나 내부 Kafka 메시지 크기 추가 제한은 두지 않는다.
  기존 HTTP 본문 512 KiB와 Kafka·클라이언트 자체 한도는 유지한다. 최대 유효 HTTP
  입력이 내부 메시지로 직렬화된 뒤에도 전달 가능한지 검증한다.

### 처리 실패 정책

2026-11-22 [Issue #349](https://github.com/jolvy/mosemo/issues/349)에서 Consumer가 처리하지 못한 배치의 정책을 정했다. 분류와 재시도는
Consumer 진입점(`src/mosemo/entrypoints/consumer/activity_batches.py`)에 둔다.
`mosemo.platform.messaging`은 업무 예외를 모르며, `MessageConsumer`의 계약(처리 함수가
정상 반환하면 ACK, 예외가 올라오면 ACK 없이 종료)은 바꾸지 않는다.

| 종류 | 해당 예외 | 처리 |
| --- | --- | --- |
| 영구 실패 | `InvalidBatchMessageError`, `TerminalBatchError` | 재시도 없이 로그를 남기고 ACK한 뒤 다음 메시지로 넘어간다 |
| 일시 실패 | `ActivityTimelineBusyError`, `sqlalchemy.exc.DBAPIError`, `sqlalchemy.exc.TimeoutError`(연결 풀 대기 초과), `OSError` | 프로세스를 유지한 채 같은 메시지를 재시도한다. 끝까지 실패했을 때 `DBAPIError`의 SQLSTATE가 22 또는 23으로 시작하면 ERROR 로그를 남기고 ACK한 뒤 건너뛴다. 그 밖에 끝까지 실패하면 ACK 없이 프로세스를 끝낸다 |
| 그 밖의 예외 | 위에 없는 모든 `Exception` | 재시도 없이 ERROR 로그를 남기고 ACK한 뒤 다음 메시지로 넘어간다 |

- 실패한 배치의 원문은 어디에도 따로 보관하지 않는다. DB 테이블과 별도 Kafka topic을
  만들지 않는다. 건너뛴 메시지는 토픽 보관 기간 동안 Kafka에 남고, 로그의 위치 정보로 찾는다.
- 같은 Device의 뒤 배치를 보류하지 않는다. 건너뛴 배치만 빠지고 뒤 배치는 정상 처리한다.
- 그 밖의 예외(분류에 없는 예외, 곧 코드 버그)도 Consumer를 멈추지 않고 건너뛴다.
  Consumer가 끝나는 경우는 일시 실패를 5회 다 썼고 마지막 예외가 SQLSTATE 22·23인
  `DBAPIError`가 아닌 때뿐이다.
- 재시도는 최초 시도를 포함해 총 5회이고 시도 사이 대기는 1·2·4·8초다. jitter는 두지
  않는다(Consumer가 하나라 동시 재시도가 몰리지 않는다). 시도마다 새 DB 세션과 새 트랜잭션을 쓴다.
  1~4회째의 DB 오류는 SQLSTATE와 관계없이 재시도하고, SQLSTATE는 5회째 실패에서만 본다.
  SQLSTATE는 `getattr(error.orig, "sqlstate", None)`으로 읽으며 값이 없거나 문자열이 아니면
  건너뛰지 않는다.
- 예외를 잡았을 때 현재 task가 취소 중이면 건너뛰거나 재시도하지 않고 그대로 올려,
  partition 회수나 종료의 취소가 세션 정리 중에 다른 예외로 바뀌어도 ACK하지 않는다.
  대기 중 취소는 그대로 전파한다. 종료 신호가 와도 진행 중인 메시지의 재시도는 멈추지 않고
  drain 30초가 지나 취소될 때 끝난다. 잠금 경합의 최악은 3초×5 + 대기 15초 = 30초로
  Kubernetes 종료 유예 45초 안이다.
- 건너뜀(영구 실패는 WARNING, 그 밖의 예외와 DB 데이터 오류는 ERROR. DB 데이터 오류는
  `error_code`에 다섯 글자 SQLSTATE를 싣고 예외 정보를 넣지 않는다), 재시도(WARNING), 중단(ERROR)을
  `operation="activity_batch_consume"`과 메시지 위치(`topic`, `partition`, `offset`)를 담아
  한 줄씩 로그로 남긴다. 계정·기기 식별자와 메시지 내용은 넣지 않는다.

근거:

- 보관하지 않는 이유: 영구 실패는 데이터 자체가 규칙에 어긋난 경우라 같은 원문을 다시 처리해도 똑같이 실패한다. 건너뛴 메시지는 토픽 보관 기간 동안만 Kafka에 남아 로그의 위치로 다시 읽을 수 있다. 저장소에 보관 기간 설정이 없어 브로커 기본값을 따르므로 그 기간은 한정된다. 보관 테이블은 창 제목·URL이 든 원문을 한 곳 더 쌓아 보관 기한 정책을 요구한다. parking topic은 보관 기간을 따로 두고 실패분만 모아 볼 수 있지만, 영구 실패를 재처리하지 않기로 했으므로 그 이점이 필요 없다.
- 보류하지 않는 이유: 관찰 타임라인은 sequence 연속성을 요구하지 않고 역순 도착을 받아 재구성하므로(요구사항 FR-8, ADR 0008) 뒤 배치가 앞 배치를 기다릴 필요가 없다. 보류는 선두 배치의 재처리 성공으로만 풀리는데 영구 실패는 재처리해도 성공하지 않아, 풀리지 않는 보류가 쌓인다.
- 그 밖의 예외를 건너뛰는 이유: 멈추면 한 계정의 데이터에서만 터지는 버그 하나가 고쳐서 배포할 때까지 모든 계정의 처리를 막는다. 건너뛰면 그 배치만 빠지고, 메시지는 토픽 보관 기간 동안 Kafka에 남아 버그를 고친 뒤 Consumer group의 offset을 로그의 위치로 되돌려 다시 처리할 수 있다. 다시 처리해도 receipt가 이미 처리한 배치를 걸러 중복이 생기지 않는다.
- 그 대가로 감수하는 것: 모든 배치에서 터지는 버그를 배포하면 쌓인 배치를 전부 건너뛴다. 건너뜀은 Pod 재시작 같은 겉으로 보이는 신호가 없어 로그를 봐야 안다. 복구는 토픽 보관 기간 안에서만 된다.
- 일시 실패를 다 쓰면 건너뛰지 않고 끝내는 이유: DB 중단 같은 장애는 배치의 문제가 아니다. 건너뛰면 장애가 이어지는 동안 쌓인 배치를 전부 넘겨 버린다. 끝내면 offset이 그대로라 장애가 풀린 뒤 이어서 처리한다. 예외는 SQLSTATE 22·23인 DB 오류로, 아래 근거에 따라 건너뛴다.
- SQLSTATE 22·23을 건너뛰는 이유: SQLSTATE 22·23은 DB가 그 배치의 값이나 제약 위반을 이유로 거절한 것이라 같은 배치를 다시 처리해도 같은 결과다. 끝내면 재시작할 때마다 같은 배치를 받아 모든 계정의 처리가 멈춘다. 건너뛰면 그 배치만 빠지고, 메시지는 토픽 보관 기간 동안 Kafka에 남아 원인을 고친 뒤 offset을 로그의 위치로 되돌려 다시 처리할 수 있다.
- 코드 버그와 같게 다루는 이유: 알려진 거절 사유(기기 미소유, 집중 세션 없음, 시간 역행, 내용 충돌)는 `TerminalBatchError`로 먼저 걸러지고 레코드 생성은 `ON CONFLICT DO NOTHING`이라, 5회 뒤에도 남는 22·23은 대부분 코드(관찰 투영과 같은 트랜잭션에서 도는 analytics 핸들러 포함)가 만든 값이나 입력 검증이 놓친 값을 DB가 거절한 경우다. 예를 들어 `activity_timeline_segments`의 CHECK(`time_order`, `kind_fields`, `last_observed_order`)와 지연 외래 키는 투영 코드의 출력을 DB가 한 번 더 검사한다. 같은 버그가 Python 예외로 드러나면 「그 밖의 예외」로 건너뛰는데 DB 제약에서 드러나면 Consumer가 멈추는 차이를 없앤다.
- 22·23을 고른 기준: JDBC 표준(`java.sql`)은 SQLSTATE 22를 `SQLDataException`, 23을 `SQLIntegrityConstraintViolationException`으로 나누고 둘을 `SQLNonTransientException`(원인을 고치지 않으면 같은 작업을 다시 해도 실패한다) 아래에 둔다. 연결(08)과 트랜잭션 롤백(40)은 이 분류에 들지 않는다.
- 재시도를 먼저 하는 이유: 제약 위반은 동시 쓰기와 부딪쳐 한 번만 날 수도 있다. 5회 안에 풀리면 건너뛰지 않는다.
- 나머지 DB 오류를 끝내는 이유: 연결 끊김·교착·잠금 같은 오류는 DB나 환경의 문제다. 건너뛰면 장애가 이어지는 동안 쌓인 배치를 전부 넘겨 버린다.
- DB 데이터 오류를 건너뛰며 감수하는 것: 코드 버그가 22·23 오류로 나타나면 그 배치는 조용히 빠지고 ERROR 로그로만 드러난다. 모든 배치에서 그런 버그가 나면 쌓인 배치를 전부 건너뛴다(배치마다 5회 재시도 15초가 걸린다). 코드보다 앞선 마이그레이션이 NOT NULL 열이나 CHECK를 더한 경우처럼 스키마와 코드가 어긋나도 같은 일이 생기며, 이때 원인은 배치가 아니라 환경이다.
- 확인한 사실(2026-11-28, PostgreSQL 18.6·asyncpg·SQLAlchemy): 중복 키 23505, 외래 키 23503, NOT NULL 23502, CHECK 23514, 숫자 범위 22003, 문자열 안의 NUL 22021이 모두 `DBAPIError`(23 갈래는 하위 클래스 `IntegrityError`)로 올라오고 `orig.sqlstate`에 코드가 실린다. 지연 외래 키(`DEFERRABLE INITIALLY DEFERRED`) 위반은 문장 실행이 아니라 COMMIT에서 나며, 이때도 `IntegrityError`의 `orig.sqlstate`에 23503이 실린다.
- 재시도 5회·1·2·4·8초는 보존해 둔 요구 FR-15의 값이다.

### 후속 범위와 미결정

- 연속 건너뛰기 상한, 분류에 없는 인프라성 예외의
  추가 구분, 건너뛴 배치를 다시 처리하는 도구, 실패한 배치를 클라이언트에 알리는 API와 배치 상태
  조회, 토픽 보관 기간 설정, 수락 뒤 Consumer에 도착하지 않은 배치의 대응, 경보
  연동은 이 결정에 넣지 않는다. 입력 검증을 통과했지만 DB가 거절하는 값은 5회 재시도 뒤 건너뛰며, 이 값을 수락 전에 거절하는 일은 [Issue #350](https://github.com/jolvy/mosemo/issues/350)에서 다룬다.
- group/topic 이름, broker 이미지 버전과 환경별 retention·디스크 상한은 실행 구성에서
  구체화한다. 발행 확인 5초·Consumer 종료 30초와 추가 제한을 두지 않는 결정은 확정됐다.
- 재할당 중 진행 중 처리와 종료·offset 안전성은 구현 검증 대상이다.

이 ADR의 결정과 초기 실행 수치는 승인됐다. 처리 실패 정책은 「처리 실패 정책」의 분류를 따른다.

## 고려한 대안

| 대안 | 장점 | 비용·제약 | 선택 |
| --- | --- | --- | --- |
| 메모리 큐와 TestBroker 유지 | 외부 Kafka 없이 실행 가능하고 현재 동작을 유지한다. | API 종료 시 미처리 원본 유실, 실제 브로커·offset·재할당 검증 불가. | 전환 대상 |
| 독립된 Kafka 브로커에 발행 후 ACK를 기다림 | 수락 원본을 API 메모리와 분리하고 환경별 브로커에 같은 전달 계약을 적용한다. | 브로커 왕복 지연, Kafka 인프라·저장 공간, consumer 복구 설계가 필요하다. | 채택 방향 |
| HTTP 요청에서 DB 처리 완료 후 응답 | 응답과 DB 결과의 관계가 단순하고 별도 전달 인프라가 없다. | 응답이 전체 계산·DB 처리 시간에 종속되며 수락과 비동기 처리를 분리하는 목표에 맞지 않는다. | 미채택 |
| 지금 설정(`acks=all`과 producer idempotence) 유지 | 수락한 배치가 리더 장애로 사라지지 않는다. | 복제본을 늘리면 복제 대기만큼 발행이 느려진다. | 미채택 |

## 결과와 트레이드오프

- 별도 동시 발행 수 제한을 두지 않아 구현은 단순하지만 broker 지연 시 API 대기
  요청이 증가할 수 있다. 발행 5초 제한은 API 전체 메모리 상한을 보장하지 않는다.
- 내부 메시지 크기 추가 한도를 두지 않으므로 별도의 애플리케이션 거절 기준은
  만들지 않는다. Kafka·클라이언트 자체 제약과 직렬화 크기의 호환성은 검증한다.
- Consumer 종료 30초는 초기 실행값이며 처리량·응답 지연의 성능 보장은 아니다.

- 정상 HTTP 수락 경로가 Kafka 가용성과 발행 ACK 지연에 의존한다. 메모리 수락
  응답과 같은 지연이라고 가정하지 않는다.
- 수락한 원본은 API 프로세스 메모리에만 존재하지 않는다. 보존·장애 내성의 범위는
  연결한 Kafka의 복제·저장·retention 설정에 달려 있다. 로컬 Docker 개발 구성의
  단일 브로커·volume 특성을 운영 환경의 보장으로 일반화하지 않는다.
- 현재 메모리 경로의 Worker 재시도·실패 보관·Device별 보류는 그대로 사용할 수
  있는 Kafka offset 정책이 아니다. 처리 실패는 「처리 실패 정책」대로 건너뛰거나 재시도하고,
  정상 전달 경로와 함께 실제 브로커로 검증한다.
- 검증에는 발행 ACK 전후 응답, ACK 미확인 후 동일 배치 재시도, API 재시작,
  DB commit 직후 consumer 종료와 재전달, 두 partition의 순차·병렬 처리와
  offset 재개·정상 종료를 포함한다. 영구 실패 배치를 건너뛰고 같은 Device의 뒤 배치를
  처리하는 경로도 실제 브로커로 검증한다.
- 최초 이력 정렬 전환의 부분 재구성 최적화는 별도 과제이며 이번 전달 경로
  변경과 결합하지 않는다.
- 발행은 `acks=1`을 쓰고 producer idempotence를 쓰지 않는다. 리더의 확인만 기다려 발행
  지연을 줄이고, 수락 뒤 드물게 생기는 유실은 감수한다.
  - 감수하는 것 1: 복제본이 둘 이상일 때 리더가 확인을 보낸 직후 복제 전에 죽으면, 202를
    받은 배치가 사라진다. 클라이언트는 202 뒤 로컬 배치를 지우므로 되살릴 방법이 없다.
  - 감수하는 것 2: producer가 확인을 받지 못해 다시 보내면 같은 배치가 토픽에 두 번 기록될
    수 있다. 두 번째는 receipt가 걸러 DB에는 한 번만 반영된다([ADR 0007](0007-store-batch-receipts-with-activity-transactions.md)).
  - 측정(2026-11-22, 로컬 단일 브로커·replication factor 1·20KB 메시지·설정별 1,800건 순차
    발행): `acks=all`+idempotence는 p50 1.65ms·p99 3.46ms, `acks=1`은 p50 1.46ms·p99
    2.11ms였다. 복제본이 하나일 때는 차이가 거의 없다. 차이는 복제본을 늘려 `acks=all`이
    복제를 기다리게 될 때 생긴다.

## 대체한 ADR

없음. 메모리 전달만을 규정한 기존 ADR은 없다. ADR 0004의 쓰기 경계와 원본
정렬·이력 전환 변경은 ADR 0007·0008에서 다룬다.

## 연관 PRD·ADR

- [배치 receipt·트랜잭션 ADR](0007-store-batch-receipts-with-activity-transactions.md)
- [관찰 순서·이력 전환 ADR](0008-order-observations-and-transition-existing-timelines.md)
- [활동 배치 PRD](../prd/activity-batches.md)
- [활동 배치 요구사항](../requirements/activity-batches.md)
- [쓰기 시 관찰 projection ADR](0004-project-observation-timeline-on-write.md)
- [Apache Kafka Docker 이미지](https://kafka.apache.org/41/getting-started/docker/)
- [Apache Kafka producer ACK 설정](https://kafka.apache.org/40/configuration/producer-configs/)

2026-10-18 승인된 계약을 반영했다. 실패·복구는 후속이며 구현 검증 전이다.

2026-10-18 사용자가 ADR 0006~0009 모두를 명시적으로 승인하여 accepted로 전환했다. 구현·검증 완료를 뜻하지 않는다.

2026-10-18 사용자의 “0006에 반영해” 지시에 따라 본 ADR을 직접 갱신했다. 발행 확인
5초·Consumer 종료 대기 30초·Publisher 동시 발행 및 내부 메시지 크기 추가 제한 없음의
승인을 반영하며, 새 ADR을 생성하지 않는다. 구현·검증 완료를 뜻하지 않는다.

2026-11-22 [Issue #346](https://github.com/jolvy/mosemo/issues/346)에 따라 Kafka 경로도 트랜잭션 시작에 계정 advisory 잠금을 잡도록 반영했다. 근거는 [ADR 0016](0016-compute-activity-analytics-on-read-and-store-daily-stats.md)이다.

2026-11-22 [Issue #349](https://github.com/jolvy/mosemo/issues/349)에 따라 처리 실패 정책(영구 실패와 그 밖의 예외는 건너뛰고, 일시 실패는 5회까지 재시도한 뒤 ACK 없이 종료)을 정하고 빈 실패·복구 경계 객체를 지웠다.

2026-11-22 [Issue #369](https://github.com/jolvy/mosemo/issues/369)에 따라 발행을 `acks=all`·producer idempotence에서 `acks=1`·producer idempotence 미사용으로 바꾸고, 감수하는 유실·중복과 측정값, 기존 설정 유지 대안을 더했다.

2026-11-28 [Issue #380](https://github.com/jolvy/mosemo/issues/380)에 따라 일시 실패를 5회 다 쓴 마지막 예외가 SQLSTATE가 22 또는 23으로 시작하는 `DBAPIError`이면 ERROR 로그를 남기고 ACK한 뒤 건너뛰도록 정하고 근거를 더했다.
