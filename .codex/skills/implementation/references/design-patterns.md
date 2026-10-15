# Design Patterns

## What Are Design Patterns?

Reusable solutions to common design problems. A shared vocabulary for discussing design.

## WARNING: Don't Force Patterns

> "Let patterns emerge from refactoring, don't force them upfront."

Patterns should solve problems you HAVE, not problems you MIGHT have.

## When to Use Patterns

1. **You recognize the problem** - You've seen it before
2. **The pattern fits** - Not forcing it
3. **It simplifies** - Doesn't add unnecessary complexity
4. **Team understands it** - Shared knowledge

---

## Creational Patterns

### Singleton

**Purpose:** Ensure only one instance exists.

**When to use:** Global configuration, connection pools, logging.

**Warning:** Often overused. Consider dependency injection instead.

```python
from functools import lru_cache


@lru_cache
def get_settings() -> Settings:
    return Settings()


# FastAPI can inject get_settings; avoid a hand-written global singleton.
```

### Factory

**Purpose:** Create objects without specifying exact class.

**When to use:** Object creation logic is complex, or varies by type.

```python
class Notification(Protocol):
    def send(self, message: str) -> None: ...


class EmailNotification:
    def send(self, message: str) -> None: ...


class SMSNotification:
    def send(self, message: str) -> None: ...


def make_notification(kind: NotificationKind) -> Notification:
    match kind:
        case NotificationKind.EMAIL:
            return EmailNotification()
        case NotificationKind.SMS:
            return SMSNotification()
        case _:
            raise UnsupportedNotification(kind)
```

### Builder

**Purpose:** Construct complex objects step by step.

**When to use:** Objects with many optional parameters, test data creation.

```python
class UserBuilder:
    def __init__(self) -> None:
        self.name = "Alice"
        self.email = "alice@example.com"
        self.age: int | None = None

    def with_age(self, age: int) -> "UserBuilder":
        self.age = age
        return self

    def build(self) -> User:
        return User(name=self.name, email=self.email, age=self.age)


user = UserBuilder().with_age(30).build()
```

### Prototype

**Purpose:** Create new objects by cloning existing ones.

**When to use:** Object creation is expensive, or you need copies with slight variations.

```python
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Document:
    title: str
    content: str
    metadata: Metadata


updated = replace(document, title="Revised")
# For Pydantic models, model_copy(update={"title": "Revised"}) is similar.
```

---

## Structural Patterns

### Adapter

**Purpose:** Make incompatible interfaces work together.

**When to use:** Integrating third-party libraries, legacy code.

```python
class PaymentGateway(Protocol):
    def charge(self, amount: Money) -> ChargeResult: ...


class LegacyPaymentAdapter:
    def __init__(self, old_api: OldPaymentAPI) -> None:
        self.old_api = old_api

    def charge(self, amount: Money) -> ChargeResult:
        paid = self.old_api.make_payment(amount.to_cents())
        return ChargeResult.success() if paid else ChargeResult.failed()
```

### Decorator

**Purpose:** Add behavior to objects dynamically.

**When to use:** Adding features without modifying existing code.

```python
class Notifier(Protocol):
    def send(self, message: str) -> None: ...


class EmailNotifier:
    def send(self, message: str) -> None: ...


class SMSDecorator:
    def __init__(self, wrapped: Notifier) -> None:
        self.wrapped = wrapped

    def send(self, message: str) -> None:
        self.wrapped.send(message)
        send_sms(message)


notifier = SMSDecorator(EmailNotifier())
notifier.send("Alert")
```

### Proxy

**Purpose:** Control access to an object.

**When to use:** Lazy loading, access control, logging, caching.

```python
class Image(Protocol):
    def display(self) -> None: ...


class LazyImage:
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self._real: RealImage | None = None

    def display(self) -> None:
        if self._real is None:
            self._real = RealImage(self.filename)
        self._real.display()
```

### Composite

**Purpose:** Treat individual objects and compositions uniformly.

**When to use:** Tree structures, hierarchies (files/folders, UI components).

```python
class Priced(Protocol):
    def price(self) -> Money: ...


class Product:
    def price(self) -> Money:
        return self._price


class Box:
    def __init__(self, children: list[Priced]) -> None:
        self.children = children

    def price(self) -> Money:
        return sum_money(child.price() for child in self.children)


box = Box([Box([product_a, product_b]), product_c])
```

---

## Behavioral Patterns

### Strategy

**Purpose:** Define a family of algorithms, make them interchangeable.

**When to use:** Multiple ways to do something, switchable at runtime.

```python
class PricingStrategy(Protocol):
    def calculate(self, base_price: Money) -> Money: ...


class RegularPricing:
    def calculate(self, base_price: Money) -> Money:
        return base_price


class PremiumDiscount:
    def calculate(self, base_price: Money) -> Money:
        return base_price.multiply(Decimal("0.8"))


class ShoppingCart:
    def __init__(self, pricing: PricingStrategy) -> None:
        self.pricing = pricing

    def total(self) -> Money:
        return self.pricing.calculate(sum_money(item.price for item in self.items))
```

### Observer

**Purpose:** Notify multiple objects about state changes.

**When to use:** Event systems, pub/sub, reactive updates.

```python
class Observer(Protocol):
    def update(self, event: OrderPlaced) -> None: ...


class EventEmitter:
    def __init__(self) -> None:
        self.observers: list[Observer] = []

    def notify(self, event: OrderPlaced) -> None:
        for observer in self.observers:
            observer.update(event)


# For cross-process delivery, use a durable queue or outbox instead.
```

### Template Method

**Purpose:** Define algorithm skeleton, let subclasses override steps.

**When to use:** Common algorithm with varying steps.

```python
class DataExporter(ABC):
    def export(self, data: list[Record]) -> None:
        self.validate(data)
        self.write(self.format(data))

    @abstractmethod
    def format(self, data: list[Record]) -> str: ...

    @abstractmethod
    def write(self, content: str) -> None: ...


class CSVExporter(DataExporter):
    def format(self, data: list[Record]) -> str:
        return "\n".join(record.to_csv() for record in data)

    def write(self, content: str) -> None:
        Path("export.csv").write_text(content)
```

### Command

**Purpose:** Encapsulate a request as an object.

**When to use:** Undo/redo, queuing, logging actions.

```python
class Command(Protocol):
    def execute(self) -> None: ...
    def undo(self) -> None: ...


@dataclass
class AddItem:
    cart: Cart
    item: Item

    def execute(self) -> None:
        self.cart.add(self.item)

    def undo(self) -> None:
        self.cart.remove(self.item)


history: list[Command] = []
command = AddItem(cart, item)
command.execute()
history.append(command)
```

---

## Pattern Awareness

### The Four-Dimensional Lens

When analyzing new code/libraries, ask:

1. **What problem does it solve?** (Creational, Structural, Behavioral)
2. **What scope?** (Object-level, Class-level, System-level)
3. **When is it applied?** (Compile-time, Runtime)
4. **How coupled?** (Tight, Loose)

This helps recognize patterns even in unfamiliar code.

---

## Anti-Patterns to Avoid

| Anti-Pattern | Problem | Solution |
|--------------|---------|----------|
| **God Object** | Class does everything | Split by responsibility |
| **Spaghetti Code** | Tangled, no structure | Refactor to layers |
| **Golden Hammer** | Using one pattern for everything | Match pattern to problem |
| **Premature Optimization** | Optimizing before needed | YAGNI, profile first |
| **Copy-Paste Programming** | Duplication | Extract, Rule of Three |
