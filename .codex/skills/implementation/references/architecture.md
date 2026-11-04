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

책임을 나누거나 인자 수·복잡도를 줄일 때 다음 역할 규칙을 따른다. 괄호의 규칙 ID는 `validate_conventions.py`가 검사한다.

| 역할 | 규칙 |
| --- | --- |
| service | 트랜잭션, flush 순서, 현재 시각 읽기, 응답 스키마 변환만 맡는다. 업무 처리는 트랜잭션을 열지 않는 하위 협력자에게 맡긴다. router는 모듈당 service 하나를 주입받는다. |
| 하위 협력자 | 트랜잭션을 열지 않고 시각을 읽지 않으며 `now`를 인자로 받는다. FastAPI provider로 주입한다(provider가 repository를 직접 만드는 기존 모듈은 그 방식을 따른다). |
| 묶음 객체 금지 | 필드가 의존성(repository, service, session, clock, config, client 등)인 데이터 클래스로 인자 개수를 줄이지 않는다. 업무 사건의 데이터를 담는 값 객체는 허용한다(CS-P27). |
| Mapper | API 요청 스키마로 엔티티를 만드는 일은 모듈 `mappers.py`의 `XxxMapper.to_entity`가 맡는다. 상태 없는 클래스이고 사용하는 쪽이 직접 만든다. 만든 엔티티는 session에 add하지 않고 repository에 넘긴다. 응답 방향은 기존 `Response.model_validate(entity, from_attributes=True)` 관례를 유지한다. `mappers.py`는 `async def`를 두지 않고 repository·`sqlalchemy`·`mosemo.infra.database`·`mosemo.infra.persistence`·`mosemo.infra.clock`·`mosemo.shared.application.clock`을 import하지 않는다. |
| repository 신규 저장 인자 | API 스키마를 받지 않는다. INSERT 값이 충돌 키뿐이면 키를 키워드 인자로(`accounts`, `devices` 현행), 그 밖이면 Mapper가 만든 미저장 엔티티 하나를 받는다. INSERT에는 현재 INSERT가 명시하는 컬럼만 넣는다(엔티티에 값이 없는 server default·Python default 컬럼을 NULL로 넣지 않는다). |
| repository의 판단 | 조회한 엔티티·값 객체의 필드를 Python 식으로 비교해 업무 결정을 내리지 않고, 그 결정은 엔티티·값 객체의 메서드를 호출해 얻는다. 조회 조건(SQL), 키 조회, 사용한 ID 집합 같은 순회 관리는 대상이 아니다. |
| models | API 스키마(`schemas` 모듈)를 import하지 않는다. 생성자 `__init__`·`__new__`에 `@override`를 붙여 인자 수 규칙을 피하지 않는다(CS-P31). |
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
