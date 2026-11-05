# Software Architecture

## The Goal of Architecture

Enable the development team to:
1. **Add** features with minimal friction
2. **Change** existing features safely
3. **Remove** features cleanly
4. **Test** features in isolation
5. **Deploy** independently when possible

## Architectural Principles

### 1. Vertical Boundaries (Features/Slices)

Organize by **feature** when it makes related changes easier to find; preserve an existing project's conventions.

```
BAD: Layer-first
src/
  routers/
    users.py
    orders.py
  services/
    users.py
    orders.py
  repositories/
    users.py
    orders.py

GOOD: Feature-first
src/
  users/
    router.py
    schemas.py
    service.py
    repository.py
  orders/
    router.py
    schemas.py
    service.py
    repository.py
```

**Why:** Changes to "users" feature stay in `users/`. High cohesion within features.

### 2. Horizontal Boundaries (Layers)

Separate concerns into layers with clear dependencies.

```
┌──────────────────────────────────────┐
│           Presentation               │  FastAPI routers, schemas, CLI
├──────────────────────────────────────┤
│           Application                │  Use Cases, Orchestration
├──────────────────────────────────────┤
│             Domain                   │  Business Logic, Entities
├──────────────────────────────────────┤
│          Infrastructure              │  Database, APIs, External
└──────────────────────────────────────┘
```

### 3. The Dependency Rule

**Dependencies point INWARD.**

```
Infrastructure → Application → Domain
      ↓               ↓            ↓
   (outer)        (middle)      (inner)
```

- Inner layers know NOTHING about outer layers
- Domain has zero dependencies on infrastructure
- Use a narrow `Protocol` or callable when a boundary actually needs inversion

```python
from typing import Protocol


class UserRepository(Protocol):
    def add(self, user: User) -> None: ...
    async def find_by_id(self, user_id: UserId) -> User | None: ...


class PostgresUserRepository:
    def add(self, user: User) -> None:
        # SQLAlchemy persistence here
        ...


class UserService:
    def __init__(self, repo: UserRepository) -> None:
        self.repo = repo
```

### 4. Contracts

Interfaces define boundaries between components.

```python
class PaymentGateway(Protocol):
    async def charge(self, amount: Money, card: CardDetails) -> ChargeResult: ...
    async def refund(self, charge_id: str) -> RefundResult: ...


class StripeGateway: ...  # implements the same contract


class PayPalGateway: ...


class FakeGateway: ...  # deterministic tests
```

### FastAPI Request Boundaries

- A router translates HTTP input and output and delegates use-case work. Keep domain decisions and transaction policy out of endpoint functions.
- Use Pydantic request and response models for validation and serialization at the API boundary. Keep them as data contracts; domain invariants belong with the objects or policies that own them.
- Use `Depends` to supply request-scoped resources such as the authenticated principal or database session. `Annotated[T, Depends(...)]` keeps the type visible; dependencies should express a reusable boundary, not hide an unrelated workflow.
- Use `async def` when the route awaits non-blocking I/O. Do not call blocking clients directly from an async endpoint; use a synchronous endpoint or offload only the blocking call.
- FastAPI `BackgroundTasks` run in the API process without durable retry. Use a persistent worker or queue when work must survive process exit or needs retry guarantees.

### 5. Cross-Cutting Concerns

Concerns that span multiple features: logging, auth, validation, error handling.

**Options:**
- FastAPI middleware and exception handlers
- Dependencies for request-scoped authentication and resources
- Decorators for pure cross-cutting behavior where appropriate

```python
@app.middleware("http")
async def log_request(request: Request, call_next):
    logger.info("request", extra={"path": request.url.path})
    response = await call_next(request)
    logger.info("response", extra={"status": response.status_code})
    return response
```

### 6. Conway's Law

> "Organizations design systems that mirror their communication structure."

**Implication:** Team structure affects architecture. Align both intentionally.

---

## Common Architectural Styles

### Layered Architecture

Traditional layers: Presentation → Business → Persistence

**Pros:** Simple, well-understood
**Cons:** Can become a "big ball of mud" without discipline

### Hexagonal Architecture (Ports & Adapters)

Domain at center, adapters around the edges.

```
        ┌─────────────────────┐
        │     HTTP Adapter    │
        └─────────┬───────────┘
                  │
┌─────────────────▼─────────────────┐
│              DOMAIN                │
│   ┌─────────────────────────┐     │
│   │      Business Logic      │     │
│   │      Use Cases           │     │
│   └─────────────────────────┘     │
└─────────────────┬─────────────────┘
                  │
        ┌─────────▼───────────┐
        │   Database Adapter   │
        └─────────────────────┘
```

**Ports:** Interfaces defined by the domain
**Adapters:** Implementations that connect to the outside world

### Clean Architecture

Similar to Hexagonal, with explicit layers:

1. **Entities** - Enterprise business rules
2. **Use Cases** - Application business rules
3. **Interface Adapters** - Controllers, Presenters, Gateways
4. **Frameworks & Drivers** - Web, DB, External interfaces

---

## Feature-Driven Structure (FastAPI)

```
src/
  app/
    users/
      router.py         # HTTP boundary
      schemas.py        # Pydantic request and response models
      service.py        # Use cases
      domain.py         # Rules, if distinct from persistence
      repository.py     # Persistence boundary when needed
    orders/
      router.py
      schemas.py
      service.py
    database.py
    main.py
```

Create only files a feature actually needs. An existing repository's structure takes precedence over this example.

### Mosemo 코드 역할 규칙

Mosemo는 Bounded Context마다 presentation·application·infrastructure·domain 계층을 둔다(위 예시 구조보다
우선한다). 패키지 구조·의존 방향·조립은 ADR 0011(`docs/adr/0011-split-bounded-contexts-logically-with-layered-packages.md`),
역할과 트랜잭션·영속화는 ADR 0013(`docs/adr/0013-persist-dataclass-aggregates-with-imperative-mapping.md`),
도메인 이벤트는 ADR 0012(`docs/adr/0012-notify-cross-context-changes-with-in-process-domain-events.md`)를 따른다.
책임을 나누거나 인자 수·복잡도를 줄일 때 다음 역할 규칙을 따른다. 괄호의 `CS-P` 규칙 ID는 `validate_conventions.py`가,
괄호의 도구 이름은 그 도구가, ADR 규칙은 import-linter 계약·`validate_architecture.py`·`tests/unit/harness/`의 내성 테스트가 검사한다.

| 역할(위치) | 규칙 |
| --- | --- |
| router(`presentation`) | HTTP 입출력만 바꾸고 UseCase의 `execute`만 호출(await)한다. 공개할 오류는 도메인 오류(`...Error`)를 BC 오류 코드의 `ApiException`으로 바꾼다(CS-P07). infrastructure를 직접 import하지 않는다. router는 presentation의 router 생성 함수가 `bootstrap.py`에서 조립한 UseCase를 받아 만든다. |
| Mapper(`presentation`) | API 요청 스키마와 엔티티·application 입출력 값 사이의 상태 없는 변환을 맡는다(`XxxMapper.to_entity`, 응답 방향 `to_response`). 조회·저장·현재 시각 읽기를 하지 않는다. |
| UseCase(`application`) | `shared`의 `UseCase`를 상속하고 유스케이스 하나에 클래스 하나, 공개 메서드는 `async def execute`만 둔다. 트랜잭션은 UseCase만 쓰기는 `unit_of_work`, 읽기는 `read_snapshot`으로 연다(ADR 0013). `begin`·`commit`·`rollback`을 직접 호출하지 않는다(CS-P13). 업무 판단은 aggregate 메서드에 맡기고 UseCase는 로딩·순서·트랜잭션·현재 시각 읽기를 조율한다. |
| 공개 협력자(`application`, `public.py`로 공개) | 다른 BC의 UseCase가 자기 트랜잭션 안에서 부르는 입구다. 첫 인자로 `db_session`을 받고 트랜잭션을 열지 않으며 `execute`가 아닌 업무 이름의 메서드를 둔다. |
| Repository(`infrastructure`) | aggregate root 하나를 통째로 불러오고 저장한다. root마다 하나만 두고 상태가 없으며 판단과 트랜잭션은 하지 않는다. 세션은 메서드 첫 인자 `db_session`으로 받는다. 재시도·동시 요청이 있는 생성은 `get_or_create`의 `ON CONFLICT DO NOTHING`으로 하고 `IntegrityError`를 잡지 않는다(CS-P26). 조회한 필드를 Python 식으로 비교해 업무 결정을 내리지 않는다. |
| Query(`infrastructure`) | 화면이나 다른 BC가 쓰는 읽기 모델을 읽는다. 데이터를 바꾸지 않고 엔티티를 돌려주지 않는다. |
| 엔티티(`domain`) | dataclass aggregate와 값 객체다. `Table`과 imperative mapping은 infrastructure에 두고 `DeclarativeBase`·`mapped_column`을 쓰지 않는다(Ruff `TID251`). SQLAlchemy·FastAPI·Pydantic을 import하지 않는다. |
| 묶음 객체 금지 | 필드가 의존성(repository, session, clock, config, client 등)인 데이터 클래스로 인자 개수를 줄이지 않는다. 업무 사건의 데이터를 담는 값 객체는 허용한다(CS-P27). |
| 생성자 | `__init__`·`__new__`에 `@override`를 붙여 인자 수 규칙을 피하지 않는다(CS-P31). 시각을 읽는 객체는 키워드 전용 필수 인자 `clock`을 받는다(CS-P15). |
| 같은 타입 두 객체 비교 | 저장된 쪽을 주어로 한다(`stored.has_same_start(candidate)`). |

---

## The Walking Skeleton

Start with a minimal end-to-end slice:

1. **Thinnest possible feature** that touches all layers
2. **Deployable** from day one
3. **Proves the architecture** works

Example walking skeleton for e-commerce:
- User can view ONE product (hardcoded)
- User can add it to cart
- User can "checkout" (just logs)

From there, flesh out each feature fully.

---

## Testing Architecture

```
┌────────────────────────────────────────────┐
│            E2E / Acceptance Tests          │  Few, slow, high confidence
├────────────────────────────────────────────┤
│            Integration Tests               │  Some, medium speed
├────────────────────────────────────────────┤
│              Unit Tests                    │  Many, fast, isolated
└────────────────────────────────────────────┘
```

**Test by layer:**
- **Domain:** Unit tests (most tests here)
- **Application:** Integration tests with mocked infra
- **Infrastructure:** Integration tests with real dependencies
- **E2E:** Critical paths only

---

## Architecture Decision Records (ADRs)

Document significant decisions:

```markdown
# ADR 001: Use PostgreSQL for persistence

## Status
Accepted

## Context
We need a database. Options: PostgreSQL, MongoDB, MySQL

## Decision
PostgreSQL for:
- ACID compliance
- Team familiarity
- JSON support for flexibility

## Consequences
- Need PostgreSQL expertise
- Schema migrations required
- Excellent query capabilities
```

---

## Red Flags in Architecture

- **Circular dependencies** between modules
- **Domain depending on infrastructure**
- **Framework code in business logic**
- **No clear boundaries** between features
- **Shared mutable state** across modules
- **"Util" or "Common" packages** that grow forever
- **Database schema driving domain model**
