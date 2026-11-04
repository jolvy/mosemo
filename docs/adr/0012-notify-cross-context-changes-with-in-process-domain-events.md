---
status: accepted
date: 2026-11-04
---
# BC 간 변경은 프로세스 내 도메인 이벤트로 알리고 커밋 직전 같은 트랜잭션에서 처리한다

## 맥락

[ADR 0011](0011-split-bounded-contexts-logically-with-layered-packages.md)은 BC 간 의존을
analytics → labeling → observation → labels·identity 한 방향으로 정했다. 그런데 활동 분석은
원천이 바뀔 때 저장된 분석 결과를 다시 계산해야 하고, 그 신호는 상류 BC에서 나온다.

| 원천 변경 | 발생 BC | 발생 경로 |
| --- | --- | --- |
| 관찰 구간 재구성(늦은 관찰 포함) | observation | 활동 단건 기록, Kafka 배치, 레거시 배치 |
| 집중 세션 완료 | observation | HTTP API |
| 라벨 확정·정정 | labeling | HTTP API(단건, 일괄) |

분석 요구사항은 이 변경을 원천과 같은 트랜잭션에서 "재계산 표시"로 남겨 변경을 잃지 않기로
했다. 상류 BC가 analytics의 표시 테이블에 직접 쓰면 observation·labeling이 analytics를
import해 순환이 생긴다. 진입점마다 표시를 남기게 하면 진입점이 6곳이라 하나를 빠뜨려도 오류
없이 분석만 낡는다.

같은 요구는 앞으로도 생긴다. 다른 기능이 활동 변화를 알아야 할 수 있고, 외부 시스템에 알리는
integration event가 필요해질 수 있다.

선택 기준은 다음과 같다.

- 상류 BC가 하류 BC를 모르는가
- 원천 변경과 후속 처리가 함께 커밋되어 변경을 잃지 않는가
- 진입점이 늘어도 알림을 빠뜨릴 수 없는가
- 한 트랜잭션에서 여러 aggregate가 바뀌어도 모든 변경을 처리하는가
- 나중에 커밋 후 처리나 integration event를 넣을 때 기존 코드가 덜 바뀌는가
- 추가 인프라(브로커, 이벤트 저장소)가 필요한가

## 결정

### 이벤트 기록: domain

- 상태를 바꾸는 aggregate 메서드가 자기 이벤트 목록에 이벤트를 추가한다. domain은 추적이나
  발행 장치를 모른다.

  ```python
  @dataclass(eq=False, repr=False, kw_only=True)
  class AggregateRoot:                                  # shared, SQLAlchemy import 없음
      events: list[DomainEvent] = field(default_factory=list, init=False)

  class FocusSession(AggregateRoot):
      def complete(self, *, ended_at, work_seconds, label_id, description) -> None:
          ...
          self.events.append(FocusSessionCompleted(focus_session_id=self.id, ...))
  ```

- 이벤트는 각 BC의 `domain/events.py`에만 두고, `shared`의 `DomainEvent`를 상속한
  `@dataclass(frozen=True)`로 선언한다. 다른 BC가 구독할 이벤트는 `public.py`에서 다시
  내보낸다.
- 이벤트 이름은 `{대상 명사}{과거분사}`로 짓는다. 마지막 단어는 `-ed`로 끝나거나 검사기의
  불규칙 과거분사 허용 목록에 있어야 하며, `Event` 접미사는 붙이지 않는다.
- 이벤트를 기록하는 위치는 그 일이 일어났다는 사실을 누가 확정하느냐로 정한다.

  | 사실을 확정하는 쪽 | 기록 방법 | 예 |
  | --- | --- | --- |
  | aggregate 메서드(검증과 상태 변경을 스스로 함) | 그 메서드 안에서 `self.events.append(...)` | 집중 세션 완료, 라벨 확정 |
  | DB 결과(`ON CONFLICT`로 실제 생성됐는지) | domain의 `mark_*` 메서드를 application이 호출 | 계정 생성(`account.mark_created()`) |
  | aggregate가 없는 변경 | application이 `record_event(db_session, ...)` | 관찰 구간 재구성(`TimelineChanged`) |

  - 첫째 방법이 기본이다. 첫째로 할 수 있는 이벤트를 둘째나 셋째로 기록하지 않는다.
  - `mark_*` 메서드는 이벤트만 추가하고 상태를 바꾸지 않는다.
- 이번에 정의하는 실제 이벤트는 `AccountCreated` 하나다. identity가 새 계정을 만들었을 때
  기록하고, labels가 구독해 같은 트랜잭션에서 기본 라벨을 만든다. 다른 이벤트는 첫 구독자가
  생길 때 정의한다.

  ```python
  account, created = await self._accounts.get_or_create(
      db_session, Account(provider=provider, provider_subject=subject)
  )
  if created:
      account.mark_created()  # AccountCreated → labels handler가 기본 라벨 생성
  else:
      account.authenticate(now=now)
  ```

### 수거와 디스패치: `infra.transactions`의 `unit_of_work`

- 이벤트를 가진 aggregate root는 SQLAlchemy 세션 이벤트로 자동 추적한다. DB에서 불러올 때
  (`loaded_as_persistent`)와 세션에 추가할 때(`transient_to_pending`) `AggregateRoot`
  인스턴스를 `session.info`의 강한 참조 집합에 등록한다. 같은 리스너가 ORM이 불러온 객체에
  빈 `events` 목록을 만든다(ORM 로드는 `__init__`을 거치지 않는다).
- identity map만 훑지 않는다. identity map은 약한 참조라서, 중간 flush 뒤 더 참조되지 않는
  객체가 회수되면 그 이벤트를 잃는다.
- 추적 집합은 **세션 수명 동안** 유지하고 세션이 닫히면 함께 사라진다. 커밋 때 비우지 않는다.
  한 세션에서 트랜잭션을 여러 번 열면 두 번째 트랜잭션은 같은 root를 identity map에서
  꺼내므로 `loaded_as_persistent`가 다시 오지 않는데, 추적 집합이 남아 있어 그 root의 이벤트도
  수거된다.
- 수거는 각 root의 `events`에서 이벤트를 꺼내면서 비운다. 한 번 디스패치한 이벤트는 같은
  트랜잭션의 반복 루프에서도, 같은 세션의 다음 트랜잭션에서도 다시 디스패치되지 않는다.
- 쓰기 UseCase는 `async with unit_of_work(db_session, dispatcher):`로 트랜잭션을 연다. 블록을
  나갈 때 추적 중인 root와 `record_event`로 기록된 이벤트를 모아 BEFORE_COMMIT handler를
  실행하고, handler가 만든 후속 이벤트도 큐가 빌 때까지 반복한 뒤 커밋한다.
- `unit_of_work`와 `read_snapshot`은 세션에 이미 열린 트랜잭션(예: 그 전에 실행한 조회의
  autobegin)이 있으면 진입 시 즉시 오류를 낸다. 열린 트랜잭션을 암묵적으로 커밋하거나 이어
  쓰지 않는다.
- 예외가 나면 롤백하고 추적 중인 root의 남은 이벤트와 `record_event` 기록을 버린다. 롤백된
  트랜잭션에서 다룬 객체는 다시 쓰지 않는다.
- `AggregateRoot`와 `DomainEvent`는 `shared`에서 SQLAlchemy를 import하지 않는 모듈에 둔다.
  디스패처와 handler 타입은 `AsyncSession`을 쓰므로 그와 다른 모듈에 둔다.

### 구독과 트랜잭션 단계

- handler는 `async def`만 허용한다. `Handler[E] = Callable[[E, AsyncSession], Awaitable[None]]`로
  타입을 강제한다.
- 구독은 각 BC의 `bootstrap.register_handlers(dispatcher)`가 하고, 조립 루트가 모든 BC의 등록을
  호출한다.
- 구독 API에는 트랜잭션 단계(`Phase`)를 둔다.

  ```python
  # 기본 BEFORE_COMMIT
  dispatcher.subscribe(FocusSessionCompleted, on_focus_session_completed)
  # 지금은 등록 시 오류
  dispatcher.subscribe(LabelConfirmed, notify, phase=Phase.AFTER_COMMIT)
  ```

- 지금은 BEFORE_COMMIT만 구현한다. 같은 세션, 같은 트랜잭션에서 인라인으로 실행하며, handler가
  실패하면 원천 변경도 롤백된다. 그러므로 BEFORE_COMMIT에는 가볍고 실패 가능성이 낮은 작업
  (재계산 표시 UPSERT, 이후의 outbox 기록)만 둔다.
- AFTER_COMMIT과 AFTER_ROLLBACK으로 등록하면 조립 시점에 즉시 오류를 낸다. 실제 구독자가 생길
  때 구현하며, 그때의 원칙은 다음과 같다.
  - AFTER_COMMIT은 커밋이 성공한 뒤 트랜잭션 밖에서 실행한다. 실패하거나 프로세스가 죽으면
    되돌리거나 다시 실행할 수 없으므로 유실을 허용하는 작업(알림, 캐시 무효화)에만 쓴다. DB에
    써야 하면 새 세션과 새 `unit_of_work`를 연다.
  - 확실히 전달되어야 하는 integration event는 AFTER_COMMIT에서 브로커로 직접 보내지 않고,
    BEFORE_COMMIT handler가 같은 트랜잭션에서 outbox에 기록한 뒤 별도 중계기가 보낸다.
  - 인라인과 백그라운드 실행 방식의 선택은 AFTER_COMMIT을 구현할 때 함께 정한다.
- 이 장치는 프로세스 안의 동기 함수 호출이다. 메시지 브로커 발행이나 이벤트 저장소를
  도입하지 않는다.

### 강제 방법

| 규칙 | 도구 |
| --- | --- |
| 이벤트는 `domain/events.py`에만, `DomainEvent` 상속, `frozen` | AST 검사기 |
| 이벤트 이름의 과거분사 형태, `Event` 접미사 금지 | AST 검사기(불규칙 과거분사 허용 목록 포함) |
| `begin`, `commit`, `rollback` 직접 호출은 `infra.transactions`에서만 | AST 검사기 |
| `mark_*` 메서드는 `self.events.append`만 하고 속성에 대입하지 않음 | AST 검사기 |
| 같은 세션의 두 트랜잭션에서 각 이벤트가 정확히 한 번 디스패치됨(사이에 `gc.collect()`), 롤백 시 폐기, 이미 열린 트랜잭션에서 진입 시 오류, ORM-enabled `insert(...).returning(...)`으로 생긴 root도 추적됨 | 통합 테스트 |
| handler는 `async def` | ty(`Handler` 타입) |
| 미지원 단계 등록 금지 | 실행 시점(`subscribe`가 즉시 `ValueError`) |

리뷰로 확인할 것: 이벤트 이름이 용어집 용어를 쓰는가, BEFORE_COMMIT handler가 가벼운가,
상태 변경 메서드가 필요한 이벤트를 빠뜨리지 않는가(이벤트 발생은 domain 단위 테스트로 확인한다).

## 고려한 대안

| 대안 | 장점 | 단점·비용 |
| --- | --- | --- |
| 상류 BC가 하류 BC의 표시 repository를 직접 호출 | 가장 단순하다. | BC 순환이 생기고, 하류의 대상 계산 규칙이 상류로 번진다. 상류 테스트에 하류 테이블이 필요하다. |
| 조립 루트나 진입점이 원천 처리 뒤 표시를 차례로 호출 | import 순환이 없다. | 원천 변경 진입점 6곳이 모두 기억해야 하고, 빠뜨려도 오류 없이 분석만 낡는다. |
| 상류 BC가 변경 알림 포트(`Protocol`)를 정의하고 하류가 구현 | 순환이 없고 같은 트랜잭션에서 실행된다. | 구독자가 늘 때마다 상류의 포트를 고쳐야 한다. application이 알림을 호출해야 해서 "알림 단일 지점" 규칙과 검사기가 따로 필요하다. |
| 호출하는 쪽이 반환된 aggregate의 이벤트를 꺼내 직접 발행 | 숨은 장치가 없고 흐름이 코드에 보인다. | 한 트랜잭션에서 여러 aggregate가 바뀌는 흐름(가입, 라벨 확정 일괄, handler의 후속 변경)에서 호출자가 바뀐 aggregate를 모두 알아야 하고, 빠뜨리면 이벤트를 잃는다. |
| Repository가 불러온 root를 `uow.track()`으로 명시 등록 | 추적이 코드에 드러난다. | Repository에 UoW 객체를 넘겨야 하고, 메서드마다 등록을 빠뜨리지 않게 할 장치가 필요하다. |
| 커밋 후 발행(Cosmic Python 방식) | 원천 트랜잭션이 handler 실패의 영향을 받지 않는다. | 커밋과 발행 사이에 실패하면 변경을 잃는다. 재계산 표시 요구와 맞지 않는다. |
| 메시지 브로커(Kafka)로 발행하고 별도 Consumer가 처리 | BC가 더 느슨하게 결합되고 구독자를 늘리기 쉽다. | HTTP 경로는 outbox와 중계기가 필요하고 Consumer 실패 정책을 함께 떠안는다. 프로세스 하나에서는 비용에 비해 얻는 것이 적다. |
| DB 트리거로 표시 기록 | 빠뜨릴 수 없다. | 날짜 경계·겹치는 세션 계산 같은 업무 규칙이 SQL로 들어가 파이썬 테스트와 타입 검사 밖에 놓인다. |

## 결과와 트레이드오프

- 상류 BC는 하류 BC를 import하지 않는다. 새 구독자는 조립 루트의 등록만 추가한다.
- 상태 변경 메서드 안에서 이벤트를 기록하므로 진입점이 늘어도 알림이 빠지지 않는다.
- BEFORE_COMMIT handler의 실패와 지연은 원천 요청을 실패시키거나 늦춘다. handler를 가볍게
  유지해야 하며, 이는 테스트로 보장되지 않는다.
- 추적은 전역 SQLAlchemy 리스너와 `session.info`에 의존한다. 동작이 Repository 코드에 보이지
  않으므로 `infra.transactions` 한 모듈에 모아 두고 통합 테스트로 확인한다.
- 조회로 불러온 root도 추적 집합에 들어가고, 집합은 세션이 닫힐 때까지 유지된다. 이벤트가
  없으므로 결과는 같지만, 한 세션에서 아주 많은 root를 불러오는 흐름은 세션을 끝날 때까지
  붙잡는 객체가 늘어난다. 그런 흐름은 세션을 나눠 쓴다.
- AFTER_COMMIT, AFTER_ROLLBACK, integration event를 추가할 때는 디스패처와 `unit_of_work`, 조립
  루트의 등록만 바뀐다. domain, UseCase, 기존 handler는 바뀌지 않는다.

## 대체한 ADR

없음

## 연관 PRD·ADR

- [ADR 0011](0011-split-bounded-contexts-logically-with-layered-packages.md)
- [ADR 0013](0013-persist-dataclass-aggregates-with-imperative-mapping.md): 트랜잭션은 UseCase만
  연다.
- [ADR 0004](0004-project-observation-timeline-on-write.md): 관찰 구간 재구성이
  `TimelineChanged`의 발생 지점이다.
- [ADR 0010](0010-run-batch-jobs-in-apscheduler-process.md): 재계산 표시를 소비하는 분석 갱신은
  배치 job으로 실행한다.
