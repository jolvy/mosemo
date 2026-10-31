# Testing Strategy

## The Testing Pyramid

```
       /\
      /  \        E2E Tests (Few)
     /----\       - Full system
    /      \      - Slow, brittle
   /--------\
  /          \    Integration Tests (Some)
 /------------\   - Multiple components
/              \  - Medium speed
----------------
      Unit Tests (Many)
      - Single unit
      - Fast, isolated
```

## Test Types

### Unit Tests

Test ONE class or function in isolation.

**Characteristics:**
- Fast (milliseconds)
- No external dependencies (mocked)
- Most of your tests should be unit tests

```python
def test_order_calculates_total() -> None:
    order = Order()
    order.add_item(Item(price=100))
    order.add_item(Item(price=50))
    assert order.total() == 150
```

### Integration Tests

Test multiple components together.

**Characteristics:**
- Slower (may use real DB)
- Test boundaries between components
- Fewer than unit tests

```python
@pytest.mark.asyncio
async def test_repository_saves_and_loads_order(db_session: AsyncSession) -> None:
    repo = PostgresOrderRepository(db_session)
    order = Order.create(customer_id="customer-1")
    repo.add(order)
    await db_session.commit()

    loaded = await repo.find_by_id(order.id)
    assert loaded == order
```

### E2E / Acceptance Tests

Test the entire system from user perspective.

**Characteristics:**
- Slowest
- Most brittle (many moving parts)
- Test critical paths only

```python
@pytest.mark.asyncio
async def test_checkout_flow(client: AsyncClient) -> None:
    created = await client.post("/orders", json={"product_id": "product-1"})
    assert created.status_code == 201

    paid = await client.post(f"/orders/{created.json()['id']}/pay")
    assert paid.status_code == 200
    assert paid.json()["status"] == "confirmed"
```

### FastAPI HTTP Boundaries

Use an in-process ASGI client for route behavior. Verify the public contract—status code, response body, authentication, and authorization—rather than the order of internal calls.

```python
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_profile_returns_the_authenticated_user(app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/profile", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"id": user_id, "name": "Ada"}
```

Use `app.dependency_overrides` to replace authentication or external resources at the HTTP boundary. Clear overrides after each test. When the change depends on database constraints, query semantics, or transactions, include an integration test against the repository's real test database.

---

## Arrange-Act-Assert (AAA)

Structure EVERY test this way:

```python
def test_premium_user_receives_twenty_percent_discount() -> None:
    user = User(is_premium=True)  # Arrange
    cart = Cart(user=user, items=[Item(price=100)])

    total = cart.total()  # Act

    assert total == 80  # Assert
```

### Writing AAA Backwards

Sometimes easier to write in reverse:

1. **Assert first** - What do you want to verify?
2. **Act** - What action produces that result?
3. **Arrange** - What setup is needed?

---

## Test Naming

### Bad: Abstract, Technical

```python
# Vague names:
def test_works(): ...
def test_edge_case(): ...
def test_data_property(): ...
```

### Good: Concrete Examples, Domain Language

```python
# Concrete behavior:
def test_premium_user_receives_twenty_percent_discount(): ...
def test_empty_cart_is_rejected(): ...
def test_racecar_is_a_palindrome(): ...
```

### Format

```python
# Behavior-focused name:
def test_shipping_state_controls_tax(): ...


# Context plus expected outcome:
def test_adding_two_and_three_returns_five(): ...


# A longer scenario can use fixtures and a precise name:
def test_premium_customer_checkout_applies_discount(): ...
```

---

## Test Doubles

### Dummy

Object passed but never used.

```python
class DummyLogger:
    pass


service = UserService(repo=real_repo, logger=DummyLogger())
```

### Stub

Returns predefined values.

```python
class StubUserRepository:
    async def find_by_id(self, user_id: UserId) -> User | None:
        return User(id=user_id, name="Test")

    def add(self, user: User) -> None:
        pass
```

### Spy

Records how it was called.

```python
class EmailSpy:
    def __init__(self) -> None:
        self.recipients: list[str] = []

    async def send(self, to: str, message: str) -> None:
        self.recipients.append(to)


spy = EmailSpy()
# After the use case runs:
assert "user@example.com" in spy.recipients
```

### Mock

Verifies expected interactions.

```python
from unittest.mock import AsyncMock

mailer = AsyncMock(spec=EmailSender)
await use_case.execute(order)
mailer.send.assert_awaited_once_with("user@example.com", "Order confirmed")
```

### Fake

Working implementation (simplified).

```python
class InMemoryUserRepository:
    def __init__(self) -> None:
        self.users: dict[UserId, User] = {}

    def add(self, user: User) -> None:
        self.users[user.id] = user

    async def find_by_id(self, user_id: UserId) -> User | None:
        return self.users.get(user_id)
```

---

## Testing Strategies by Layer

### Domain Layer (Most Tests)

- Unit tests with no mocks
- Test business rules, value objects, entities
- Fast, comprehensive

```python
def test_money_adds_same_currency() -> None:
    assert Money.usd(10).add(Money.usd(20)) == Money.usd(30)


def test_money_rejects_mixed_currencies() -> None:
    with pytest.raises(CurrencyMismatch):
        Money.usd(10).add(Money.eur(10))
```

### Application Layer

- Integration tests with mocked infrastructure
- Test use case orchestration

```python
@pytest.mark.asyncio
async def test_create_order_saves_and_notifies() -> None:
    repo = InMemoryOrderRepository()
    mailer = EmailSpy()
    use_case = CreateOrder(repo, mailer)

    await use_case.execute(customer_id="customer-1", items=[item])

    assert await repo.count() == 1
    assert mailer.recipients == ["user@example.com"]
```

### Infrastructure Layer

- Integration tests with real dependencies
- Test database, API integrations

```python
@pytest.mark.asyncio
async def test_postgres_repository_persists_order(db_session: AsyncSession) -> None:
    repo = PostgresOrderRepository(db_session)
    order = Order.create(customer_id="customer-1")

    repo.add(order)
    await db_session.commit()

    assert await repo.find_by_id(order.id) == order
```

---

## High-Value Integration Tests

Focus integration tests on:

1. **Boundaries** - Where systems meet
2. **Critical paths** - Money, security, core features
3. **Complex queries** - Database operations

### Contract Tests

Verify implementations match interfaces.

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", [memory_repo_factory, postgres_repo_factory])
async def test_user_repository_contract(repo_factory) -> None:
    repo = await repo_factory()
    user = User.create(name="Test")

    repo.add(user)

    assert await repo.find_by_id(user.id) == user
    assert await repo.find_by_id(UserId("missing")) is None
```

---

## Test Builders

Create test objects easily.

```python
class OrderBuilder:
    def __init__(self) -> None:
        self.customer_id = "customer-1"
        self.items: list[Item] = []
        self.status = OrderStatus.PENDING

    def with_items(self, items: list[Item]) -> "OrderBuilder":
        self.items = items
        return self

    def paid(self) -> "OrderBuilder":
        self.status = OrderStatus.PAID
        return self

    def build(self) -> Order:
        return Order(self.customer_id, self.items, self.status)


order = OrderBuilder().with_items([item]).paid().build()
```

---

## Common Testing Mistakes

| Mistake | Problem | Solution |
|---------|---------|----------|
| Testing implementation | Brittle tests | Test behavior only |
| Too many mocks | Tests prove nothing | Use real objects when possible |
| Shared state | Flaky tests | Isolate each test |
| No assertions | False confidence | Always assert something meaningful |
| Testing trivial code | Wasted effort | Focus on logic and edge cases |
| Slow tests | Reduced feedback | Optimize, use unit tests |
