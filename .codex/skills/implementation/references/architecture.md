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
    async def save(self, user: User) -> None: ...
    async def find_by_id(self, user_id: UserId) -> User | None: ...


class PostgresUserRepository:
    async def save(self, user: User) -> None:
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
