# 날짜별 관찰 타임라인 API PRD

## Problem Statement

Mosemo 사용자는 한 날짜에 실제로 관찰된 활동의 순서, 문맥 전환, 관찰할 수 없었던
공백을 화면에서 확인하고 싶다. 현재 서버는 개인정보 필터를 통과한 활동 레코드를
단건으로 보관하지만, 계정 전체의 관찰 타임라인을 반환하는 API나 저장된 구간 읽기
모델이 없다. 매번 원본을 읽어 같은 grouping을 다시 계산하는 설계는 날짜별 반복
조회와 늦게 도착한 레코드의 경계 처리에 불리하다. 활동 흐름이나 컨텍스트 스위칭
같은 후속 의미 해석과, 실제로 관찰한 사실도 구분되어야 한다.

## Solution

인증된 사용자는 계정 시간대의 달력 날짜를 지정해 하루 전체의 관찰 타임라인을
조회한다. 서버는 모든 Device의 원본 활동 레코드를 하나의 관찰 순서로 해석하고,
동일한 관찰 문맥의 활동 구간과 명시적인 수집 공백을 저장형 projection으로
관리한다. GET은 projection을 읽어 시작·종료를 자르지 않은 구간의 배열을
반환한다. 사용자는 즉시 관찰된 문맥 전환을 볼 수 있지만, 관찰하지 못한 침묵을
활동이나 수집 공백으로 추정하지 않는다.

## User Stories

1. 인증된 사용자로서 특정 날짜의 관찰 타임라인을 조회하고 싶다. 그래야 하루의 실제 관찰 기록을 볼 수 있다.
2. 인증된 사용자로서 계정 시간대 기준 날짜를 사용하고 싶다. 그래야 내 달력의 하루와 조회 결과가 일치한다.
3. 신규 사용자로서 계정 시간대가 `Asia/Seoul`로 시작하길 원한다. 그래야 별도 설정 없이 날짜 조회를 사용할 수 있다.
4. API 소비자로서 내 계정의 시간대를 계정 조회 응답에서 확인하고 싶다. 그래야 날짜 경계를 이해할 수 있다.
5. API 소비자로서 날짜 하나만 필수 입력으로 보내고 싶다. 그래야 임의 기간 계산을 하지 않아도 된다.
6. API 소비자로서 하루 전체를 한 응답에 받고 싶다. 그래야 페이지 사이의 관찰 경계를 다시 합치지 않아도 된다.
7. API 소비자로서 구간이 날짜 경계를 넘더라도 원래 시작·종료 시각을 받고 싶다. 그래야 같은 관찰 사실의 실제 경계를 잃지 않는다.
8. API 소비자로서 날짜에 아무 구간이 없으면 빈 배열을 받고 싶다. 그래야 무관찰을 가짜 수집 공백과 구분할 수 있다.
9. API 소비자로서 미래 날짜 조회도 빈 배열로 처리하고 싶다. 그래야 별도 오류 분기를 만들지 않아도 된다.
10. 사용자로서 모든 Device의 이력을 하나의 관찰 타임라인에서 보고 싶다. 그래야 앱 재설치나 Device 변경 전후의 기록을 이어서 볼 수 있다.
11. 사용자로서 Device 변경 자체가 활동 구간을 나누지 않길 원한다. 그래야 같은 문맥의 관찰이 설치 이력 때문에 잘리지 않는다.
12. 사용자로서 타임라인에 Device 출처가 드러나지 않길 원한다. 그래야 화면이 관찰 사실에 집중할 수 있다.
13. 사용자로서 관찰된 상세 문맥을 원본 그대로 보고 싶다. 그래야 앱·창·웹 정보의 수집 상태와 비공개 처리를 혼동하지 않는다.
14. 사용자로서 불투명 활동을 정상적인 활동 구간으로 보고 싶다. 그래야 개인정보 보호로 식별 정보가 없다는 사실을 수집 실패로 오해하지 않는다.
15. 사용자로서 문맥의 어느 필드든 달라지면 새 구간을 보고 싶다. 그래야 관찰된 차이를 의미상 비슷하다는 이유로 숨기지 않는다.
16. 사용자로서 같은 문맥이 계속 관찰되면 하나의 구간으로 보고 싶다. 그래야 주기적 갱신마다 카드가 생기지 않는다.
17. 사용자로서 60초를 초과해 관찰이 끊겼다면 같은 문맥도 새 구간으로 보고 싶다. 그래야 관찰되지 않은 시간을 연속 활동으로 오해하지 않는다.
18. 사용자로서 각 활동 구간의 마지막 실제 관찰 시각을 알고 싶다. 그래야 구간 종료 경계와 활동의 마지막 확인을 구분할 수 있다.
19. 사용자로서 즉시 관찰된 탭·앱 전환을 경계가 맞닿는 활동 구간으로 보고 싶다. 그래야 관찰 문맥 전환이 있었음을 알 수 있다.
20. 사용자로서 같은 시각에 여러 문맥이 관찰돼도 0초 구간이 유지되길 원한다. 그래야 순간 전환이 사라지지 않는다.
21. 사용자로서 60초 초과 침묵 뒤의 다른 문맥은 직접 전환으로 보이지 않길 원한다. 그래야 미관찰 전환 시점을 추정하지 않는다.
22. 사용자로서 수집 중단의 명시적인 `suspended`를 수집 공백으로 보고 싶다. 그래야 실제로 관찰할 수 없었던 기간을 구분할 수 있다.
23. 사용자로서 연속된 `suspended`는 하나의 공백으로 보고 싶다. 그래야 반복된 중단 알림이 화면을 쪼개지 않는다.
24. 사용자로서 수집 공백의 최초 사유를 알고 싶다. 그래야 공백이 왜 시작됐는지 알 수 있다.
25. 사용자로서 `active` 알림만으로 공백이 끝나지 않길 원한다. 그래야 아직 활동을 관찰하지 못한 시간을 활동으로 오해하지 않는다.
26. 사용자로서 공백 뒤 첫 활동 관찰이 공백을 끝내고 새 활동을 시작하길 원한다. 그래야 공백 전후의 같은 문맥도 분리된다.
27. 사용자로서 순번의 숫자 누락만으로 공백이 생기지 않길 원한다. 그래야 실제로 저장되지 않은 누락 상태를 추측하지 않는다.
28. 사용자로서 마지막 관찰이 오래됐으면 열린 활동이 마지막 관찰 시각에 닫혀 보이길 원한다. 그래야 오래된 기록이 계속 진행 중으로 표시되지 않는다.
29. 사용자로서 종료 이벤트가 없는 최신 활동은 열린 구간으로 보고 싶다. 그래야 아직 종료 사실이 없음을 알 수 있다.
30. 사용자로서 종료 이벤트가 없는 수집 공백은 오늘까지의 날짜 조회에서 열린 공백으로 보고 싶다. 그래야 미해결 수집 중단을 알 수 있다.
31. 사용자로서 열린 구간이 무한한 미래 날짜에 나타나지 않길 원한다. 그래야 날짜 조회가 근거 없는 예측이 되지 않는다.
32. 클라이언트로서 활동 단건을 다시 보내도 최초 수락 결과를 받고 싶다. 그래야 응답 유실 후 안전하게 재시도할 수 있다.
33. 클라이언트로서 같은 이벤트 식별자를 다른 내용에 재사용하면 충돌을 받고 싶다. 그래야 원본 사실이 덮어써지지 않는다.
34. 클라이언트로서 같은 Device의 순번 충돌을 명시적으로 받고 싶다. 그래야 잘못된 전송 상태를 발견할 수 있다.
35. 클라이언트로서 지연·역순 레코드도 수락되길 원한다. 그래야 네트워크 지연이 관찰 사실을 삭제하지 않는다.
36. 클라이언트로서 계정별 projection 쓰기가 바쁠 때 재시도 가능한 오류를 받고 싶다. 그래야 같은 원본을 안전하게 다시 보낼 수 있다.
37. 다른 계정의 사용자로서 타인의 관찰 타임라인이 조회되거나 섞이지 않길 원한다. 그래야 계정별 개인정보 경계가 유지된다.
38. 개발자로서 원본과 projection이 함께 저장되거나 함께 롤백되길 원한다. 그래야 부분 성공으로 잘못된 타임라인이 남지 않는다.
39. 개발자로서 grouping 규칙을 바꿀 때 보존된 원본에서 projection을 다시 만들고 싶다. 그래야 읽기 모델을 원본보다 높은 권위로 취급하지 않는다.
40. 개발자로서 구간 식별자가 재구축·병합·분할 시 바뀔 수 있음을 알고 싶다. 그래야 이를 영구 도메인 식별자로 사용하지 않는다.
41. 개발자로서 과거 조회 시 같은 시작 시각의 순간 구간이 원본 수신 순서대로 나오길 원한다. 그래야 UUID 정렬 때문에 관찰 순서가 왜곡되지 않는다.
42. 개발자로서 공개 OpenAPI와 실제 JSON·오류·헤더가 일치하길 원한다. 그래야 생성 클라이언트가 안정적으로 계약을 소비할 수 있다.

## Implementation Decisions

- 공개 조회는 Bearer 인증을 요구하는 `GET /api/v1/activities/timeline?date=YYYY-MM-DD`다.
  `date`는 필수 달력 날짜이며 `from`, `to`, 요청별 시간대와 pagination은 없다.
  operation ID는 `activitiesGetTimeline`이고, 성공 응답은 `200`의 직접 JSON 배열이다.
- `accounts.timezone`은 IANA 시간대 이름을 저장하는 `NOT NULL` 컬럼이다. 신규
  계정의 DB 기본값은 `Asia/Seoul`이다. `GET /api/v1/accounts/me`에 읽기
  전용 `timezone`을 노출하고, 시간대 변경 API는 만들지 않는다. 조회 날짜의 현지
  자정과 다음 자정을 각각 UTC instant로 변환해 DST의 23·25시간 날짜도 다룬다.
- 응답은 `segmentType`으로 식별하는 discriminated union이다. `activity`는
  `segmentId`, `segmentType`, `startedAt`, nullable `endedAt`, 필수
  `lastObservedAt`, 원본 `context`를 반환한다. `capture_gap`은 `segmentId`,
  `segmentType`, `startedAt`, nullable `endedAt`, 첫 `suspended`의 `reason`을
  반환한다. 원본 이벤트 식별자, Device 식별자, 순번과 수신 시각은 노출하지 않는다.
- JSON 필드는 camelCase이고 응답 시각은 RFC 3339 UTC `Z`다. 날짜를 위해 구간을
  자르거나 timestamp의 실제 경계를 반올림하지 않는다. 구간 선택과 원본 순서는
  저장된 subsecond 정밀도로 계산한다. 기존 초 단위로 절삭하는 공개 timestamp
  직렬화 정책은 관찰 타임라인 경계 표현에 그대로 적용하지 않는다.
- 종료된 양의 길이 구간은 계정 현지 날짜의 UTC 경계와 겹치면 포함한다.
  `startedAt == endedAt`인 구간은 그 시각이 속한 날짜에만 포함하며 정확히
  자정이면 새 날짜에 속한다. 구간이 둘 이상의 날짜와 겹치면 각 날짜 응답에
  원래 시작·종료를 그대로 반환한다.
- 열린 활동은 `startedAt`부터 `lastObservedAt`까지 근거가 있는 날짜에서만
  선택한다. 열린 수집 공백은 시작 날짜부터 계정 현지의 오늘까지 선택하고 미래
  날짜에서는 제외한다. 유효한 날짜에 해당 구간이 없으면 `200 []`이며, 전일 또는
  미래 날짜를 채우는 합성 공백은 만들지 않는다.
- 원본의 계정 전체 순서는 `observedAt ASC`, 동률일 때 `receivedAt ASC`, 다시
  `eventId ASC`다. Device별 `sequence`는 유일성 충돌을 확인할 때만 사용하며
  계정 전체의 관찰 순서로 사용하지 않는다. 동일 시작 시각의 응답 구간도
  `first_event_id`가 가리키는 원본의 `receivedAt`, `eventId`로 정렬한다.
  이 정렬에는 원본을 조인하고 별도의 구간 순서 컬럼은 저장하지 않는다.
- grouping은 정규화된 전체 `context`의 정확한 동등성으로만 판단한다. 이벤트
  식별자, 순번, 관찰·수신 시각, 관찰 당시 시간대와 Device는 비교 대상이 아니다.
  앱·창·웹의 수집 상태나 값 중 하나라도 바뀌면 새 문맥이다. 연속된 `opaque`도
  동일 문맥으로 합칠 수 있지만 URL·제목을 의미상 정규화하거나 AI로 병합하지
  않는다.
- 동일 문맥 관찰 간격이 60초 이하면 하나의 활동 구간이다. 60초를 초과하면
  이전 활동은 `lastObservedAt`에 닫고 새 활동을 시작한다. 동일 문맥의 생존
  갱신은 최대 30초 간격을 기대하지만, 탭·앱 전환은 즉시 감시해 새 관찰로
  전송한다. GET 시 마지막 관찰 후 60초 초과한 projection의 열린 활동은
  `endedAt = lastObservedAt`로 논리적으로 닫아 반환하고, 다음 쓰기가 이를
  projection에 물리적으로 반영한다. scheduler는 없다.
- 60초 이내에 새 문맥이 관찰되면 직전 활동의 `endedAt`은 새 관찰의
  `observedAt`이고 새 구간도 그 시각에 시작한다. 직전 활동의 마지막 실제
  관찰은 별도 `lastObservedAt`에 유지한다. 두 경계가 맞닿고 중간 공백이 없으면
  관찰 문맥 전환이다. 같은 시각에 여러 문맥이 관찰되어 길이가 0인 닫힌
  활동 구간도 제거하지 않는다.
- 60초를 초과한 침묵 뒤 새 문맥은 직전 활동을 `lastObservedAt`에 닫고 새
  구간을 시작한다. 사이에는 `CaptureGap`을 추정하지 않으며, 경계가 맞닿지
  않으므로 관찰 문맥 전환도 추정하지 않는다.
- `suspended`만 수집 공백을 시작한다. 직전 활동의 마지막 관찰 후 60초 이하면
  활동을 `suspended.observedAt`에 닫고, 60초 초과면 `lastObservedAt`에
  닫는다. 공백은 `suspended.observedAt`부터 시작하며 중간 침묵을 추정
  공백으로 만들지 않는다. 연속 `suspended`는 첫 시작과 이유를 유지한 하나의
  공백이다.
- `active`는 원본으로 저장하지만 타임라인 상태를 바꾸지 않는다. 수집 공백은
  다음 실제 `activity_observation`의 `observedAt`에 끝나고 그 관찰은 새로운
  활동 구간을 시작한다. 공백 전후 문맥이 동일해도 두 활동을 병합하지 않는다.
  순번의 숫자 누락은 타임라인에 영향을 주지 않는다.
- 읽기 모델은 계정별 단일 `activity_timeline_segments` 테이블이다. 공통
  컬럼은 `segment_id` UUIDv7 PK, `account_id`, `segment_type`, `started_at`,
  nullable `ended_at`, `first_event_id`다. 활동 구간에 필수인
  `last_event_id`, `last_observed_at`, `context`와 공백에 필수인 `reason`은
  종류에 따라 nullable로 저장한다.
- `first_event_id`는 활동의 첫 동일 문맥 관찰 또는 공백의 첫 `suspended`다.
  활동의 `last_event_id`는 마지막 동일 문맥 관찰이며, 구간을 닫은 다음
  이벤트가 아니다. 공백의 `last_event_id`는 `NULL`이다. 두 이벤트 참조는
  원본 `activity_records.event_id`에 대한 FK이며 `ON DELETE NO ACTION`이다.
  계정 삭제 cascade와 개별 이벤트 삭제 전 projection 수정이 함께 가능하도록
  FK 검사는 `DEFERRABLE INITIALLY DEFERRED`로 둔다.
- `account_id`는 계정 FK이며 `ON DELETE CASCADE`다. 공통 DB CHECK는
  `ended_at IS NULL OR ended_at >= started_at`이다. 활동은
  `last_observed_at`, `context`, `last_event_id`가 필수이고 `reason`은
  `NULL`이다. 공백은 반대로 `last_observed_at`, `context`,
  `last_event_id`가 `NULL`이고 `reason`이 필수다. 활동의 마지막 관찰은
  시작 이후이며, 닫힌 경우 종료 이전 또는 동일 시각이어야 한다. JSONB
  내부 형태는 Pydantic과 애플리케이션이 검증하고 복잡한 DB JSON CHECK는 두지
  않는다. 열린 구간을 계정당 하나로 제한하는 partial unique index는 두지
  않는다.
- projection의 `segmentId`는 읽기 모델 행의 식별자이며 영구 활동 식별자가
  아니다. 단순 연장 또는 종료는 기존 ID를 유지한다. 병합은 가장 이른 기존
  구간의 ID, 분할은 원래 시작·문맥을 가진 선행 구간의 기존 ID를 유지하고
  나머지는 새 ID를 발급한다. 전체 rebuild 시 모든 ID가 바뀔 수 있다.
- 모든 유효한 활동 POST는 계정별 transaction-level
  `pg_advisory_xact_lock(bigint)`을 원본·projection 상태 조회 전에 얻는다.
  키는 `activity_timeline:{account_id}` 문자열의 안정적인 BLAKE2b 64비트
  digest를 signed bigint로 변환한다. `SET LOCAL lock_timeout = '3s'`를
  사용한다. 서로 다른 계정은 병렬이며 해시 충돌은 무관한 계정의 일시적
  직렬화만 야기한다.
- 락 안에서 Device 계정 소유권, 같은 `eventId`의 동일·상이한 재시도,
  Device별 `sequence` 충돌을 기존 의미대로 판정한다. 같은 내용의 재시도는
  최초 `receivedAt`의 성공 결과를 반환하고 projection을 바꾸지 않는다.
  실제 새 원본만 projection을 갱신한다. 락을 3초 안에 얻지 못하면 원본과
  projection을 모두 롤백하고 공통 오류 구조로
  `503 ACTIVITY_TIMELINE_BUSY`와 `Retry-After: 1`을 반환한다. 클라이언트는
  동일한 `eventId`와 body를 지수 backoff·jitter로 재시도한다.
- 늦거나 순서가 뒤바뀐 이벤트는 저장하고, 새 이벤트 직전 상태를 포함하는
  기존 구간의 시작부터 같은 `observedAt`의 모든 이벤트를 포함해 재생한다.
  새 계산 결과가 기존 다음 구간과 완전히 일치하면 중단하고, 불일치가
  이어지면 수렴할 때까지 앞으로 확장한다. 최악에는 계정의 남은 전체
  이력이 대상이 된다. 구간 변경에는 합의한 ID 보존 규칙을 적용한다.
- 초기 개발 환경에서는 기존 계정 생성 Alembic 리비전 `44f2c8ca846b`에
  `timezone` 컬럼과 `Asia/Seoul` DB 기본값을 추가하고, projection 스키마는
  새 migration으로 만든다. 타임라인의 기존 원본 데이터가 없으므로 초기
  backfill은 수행하지 않는다. POST와 GET은 동일 변경에서 새 모델로 교체하며
  feature flag, 점진적 rollout, 별도 backfill 명령이나 projection 버전 상태를
  두지 않는다. 기존 migration을 수정하지 않는 저장소 원칙의 이번 개발 단계
  예외다. 향후 grouping 규칙이 바뀌면 새 migration에서 보존된 원본으로
  전체 projection을 재생성한다.
- 공개 오류는 기존 `ErrorCode`·`ApiException`·공통 `ErrorResponse` 경계를
  따른다. 필수 날짜의 누락 또는 형식 오류는 `INVALID_ARGUMENT` 422,
  인증 오류는 기존 401이다. 503의 named OpenAPI example과
  `Retry-After` 헤더를 문서화하며, runtime OpenAPI와 snapshot을 함께
  갱신한다.

## Testing Decisions

- 좋은 테스트는 내부 메서드 호출 순서, SQL 문자열이나 projection 알고리즘의
  사소한 분기 대신 사용자가 관찰하는 응답·원본 수·구간 경계·동시성 결과를
  검증한다. 동일 입력 스트림의 최종 타임라인은 전송 순서가 달라도 같아야
  하며, 이 비교는 재계산에 따라 달라질 수 있는 `segmentId`를 제외한 관찰
  의미를 기준으로 한다. 시간이 흐르는 사례는 주입한 고정 시각으로 반복
  가능하게 만든다.
- grouping·원본/projection 원자성의 주 seam은 기존 활동 서비스에 실제
  PostgreSQL을 연결한 통합 테스트다. 현재 단건 저장·충돌·동시 재시도를
  검증하는 테스트가 선례다. 새 seam을 여러 계층에 추가하지 않는다.
- 이 seam에서 동일 문맥 병합, 모든 context 필드 차이, 불투명 활동,
  Device 변경 전후 병합, 60초 정확한 경계와 60초 초과 분할, 0초 구간,
  동일 시작 시각의 tie order를 검증한다.
- 같은 seam에서 직접 문맥 전환, 60초 초과 침묵의 비전환,
  `suspended`의 신선·오래된 활동 종료, 반복 `suspended`, 중간
  `active`, 첫 활동 관찰로 공백 종료, 공백 전후 동일 문맥 분리를 검증한다.
- 같은 seam에서 순번 숫자 누락이 공백을 만들지 않는지, 동일 재시도가
  projection을 중복 갱신하지 않는지, 늦은 이벤트가 주변 병합·분할 후
  정상 원본 순서와 같은 타임라인으로 수렴하는지 검증한다.
- PostgreSQL 동시성 테스트에서 같은 계정의 쓰기가 순차 처리되는지,
  다른 계정 쓰기는 불필요하게 대기하지 않는지, 3초 락 timeout 시 원본과
  projection이 모두 롤백되는지 검증한다. timeout의 공개 503과 헤더는
  HTTP 테스트에서 확인한다.
- 기존 FastAPI 컴포넌트 seam에서 Bearer 인증, 필수 `date`와 검증 422,
  `200 []`, 직접 배열, `segmentType` union의 정확한 camelCase JSON,
  `AccountResponse.timezone`, 원본 context·reason, 원본/Device 필드
  비노출과 503 공통 오류 envelope를 검증한다. 현재 계정·활동 API
  컴포넌트 테스트가 선례다.
- 날짜·시간대 테스트는 계정 현지 자정, 정확히 자정의 0초 구간,
  날짜 경계를 넘는 자르지 않은 구간, DST로 23·25시간이 되는 날짜,
  미래 날짜, 열린 활동의 마지막 관찰 날짜 제한, 열린 공백의 오늘까지
  포함을 검증한다.
- DB migration·제약 테스트는 기존 계정 생성 리비전의 시간대 DB 기본값,
  union별 필수·NULL CHECK, 0초 허용, 이벤트 FK의 개별 삭제 제약,
  계정 삭제 cascade, 빈 DB upgrade·downgrade·재upgrade와 단일 Alembic
  head를 검증한다. 현재 repository 및 migration fixture가 선례다.
- 기존 OpenAPI 계약 테스트를 확장해 operation ID 유일성, 날짜 입력과
  응답 모델 설명, 성공·오류 status, union discriminator, 503 named example,
  `Retry-After` header, runtime schema와 공개 snapshot 일치를 자동 검증한다.
- 구현 완료를 주장하기 전 관련 테스트와 Ruff lint·format, 타입 검사,
  build, 전체 pytest, OpenAPI export, Git whitespace 검사를 실제 실행하고
  실패하거나 실행하지 못한 항목은 명시한다. Swagger UI 수동 검수는 자동
  계약 테스트를 대체하지 않는다.

## Out of Scope

- `from`·`to` 기간 조회, 페이지네이션, 응답 envelope, 사용자가 지정하는 조회 시간대
- 계정 시간대 변경 API, Device 출처 노출, 대표 Device 선정, 여러 활성 Device의 겹치는 기록 해결
- 단일 활성 기록 Device를 인증 계층에서 강제하는 작업과 기존 JWT의 즉시 무효화
- 순번 숫자 누락이나 관찰 침묵에서 합성 `CaptureGap` 생성, `active`를 통한 공백 종료
- URL·제목·앱 이름의 의미 정규화, 활동 흐름 생성, 컨텍스트 스위칭 판단, AI 해석, 집중도·통계
- 활동 구간과 수집 공백의 별도 테이블, projection version/state table, 자동 GET rebuild, background scheduler
- batch 전송, stream watermark, 오프라인 큐, 활동 원본의 개별 삭제 API 또는 보존 정책
- macOS 클라이언트 구현, 생성 클라이언트 갱신, 코드 커밋·PR 생성

## Further Notes

- 도메인 용어는 `CONTEXT.md`를 따른다. ADR 0004가 ADR 0002의 read-time
  timeline 결정을 대체하고, 최신 Device 경계 결정인 ADR 0003과 함께 적용된다.
- 이 PRD는 구현 요청이 아니라 구현 가능한 계약 기록이다. 이 문서 작성 시점의
  작업트리는 `origin/feat/activities` 기준의 detached checkout으로 최신
  `origin/main`의 Device 경계보다 앞서 있다. 구현은 최신 Device 계약과
  transaction ownership 변경을 먼저 통합한 기준에서 진행해야 한다.
- `segmentId`는 안정적인 변경 추적 ID가 아니므로 클라이언트 영속 캐시나
  다른 도메인의 FK로 사용하면 안 된다.
- 타임라인 timestamp의 소수 초 보존은 원래 경계와 순간 전환을 정확하게
  표현하기 위한 문서화 시점의 구현 추론이다. 기존 계정·활동 응답의 초 단위
  직렬화 변경까지 요구하지 않는다.
- 이벤트 FK의 deferred `NO ACTION`은 계정 삭제에서 원본과 projection의
  cascade가 같은 트랜잭션 안에 모두 완료될 수 있게 하는 구현상 추론이다.
  실제 삭제 순서는 PostgreSQL 통합 테스트로 확인한다.
  [PostgreSQL의 FK 검사 시점](https://www.postgresql.org/docs/18/sql-createtable.html)을
  참고한다.
