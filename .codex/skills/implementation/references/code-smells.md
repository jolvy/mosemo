# Code Smells & Anti-Patterns

## What Are Code Smells?

Indicators that something MAY be wrong. Not bugs, but design problems that make code hard to understand, change, or test.

## The Five Categories

### 1. Bloaters
Code that has grown too large.

| Smell | Symptom | Refactoring |
|-------|---------|-------------|
| **Long Method** | Multiple responsibilities or hard-to-follow logic | Consider Extract Method |
| **Large Class** | Multiple unrelated reasons to change | Consider Extract Class |
| **Long Parameter List** | Parameters obscure the intent or repeat a domain concept | Consider Parameter Object |
| **Data Clumps** | Same group of variables appear together | Extract Class |
| **Primitive Obsession** | Primitives instead of small objects | Wrap in Value Object |

### 2. Object-Orientation Abusers
Misuse of OO principles.

| Smell | Symptom | Refactoring |
|-------|---------|-------------|
| **Repeated Type Dispatch** | Type checking, large `match`/`if` branches | Consider Polymorphism |
| **Parallel Inheritance** | Adding subclass requires adding another | Merge Hierarchies |
| **Refused Bequest** | Subclass doesn't use parent methods | Replace Inheritance with Delegation |
| **Alternative Classes** | Different interfaces, same concept | Rename, Extract Superclass |

### 3. Change Preventers
Code that makes changes difficult.

| Smell | Symptom | Refactoring |
|-------|---------|-------------|
| **Divergent Change** | One class changed for many reasons | Extract Class (SRP) |
| **Shotgun Surgery** | One change touches many classes | Move Method/Field together |
| **Parallel Inheritance** | (see above) | Merge Hierarchies |

### 4. Dispensables
Code that can be removed.

| Smell | Symptom | Refactoring |
|-------|---------|-------------|
| **Comments** | Explaining bad code | Rename, Extract Method |
| **Duplicate Code** | Copy-paste | Extract Method, Pull Up Method |
| **Dead Code** | Unreachable code | Delete |
| **Speculative Generality** | "Just in case" code | Delete (YAGNI) |
| **Lazy Class** | Class that does almost nothing | Inline Class |

### 5. Couplers
Excessive coupling between classes.

| Smell | Symptom | Refactoring |
|-------|---------|-------------|
| **Feature Envy** | Method uses another class's data extensively | Move Method |
| **Inappropriate Intimacy** | Classes know too much about each other | Move Method, Extract Class |
| **Message Chains** | `a.getB().getC().getD()` | Hide Delegate |
| **Middle Man** | Class only delegates | Inline Class |

---

## The Seven Most Common Code Smells

### 1. Long Method

**Symptom:** A method mixes unrelated responsibilities; line count alone is not a defect.

```python
# SMELL: validation, calculation, persistence, and notification in one method.
def process_order(order: Order, session: Session, mailer: Mailer) -> None:
    if not order.items:
        raise EmptyOrder()
    total = sum(item.price * item.quantity for item in order.items)
    total *= 1 + tax_rate_for(order.customer.state)
    session.add(OrderRow.from_order(order, total))
    mailer.send(order.customer.email, "Order confirmed")


# Each named operation has a clear owner and can change independently.
def process_order(order: Order, repository: OrderRepository, mailer: Mailer) -> None:
    order.validate()
    order.calculate_total()
    repository.save(order)
    mailer.confirm_order(order)
```

### 2. Large Class

**Symptom:** A class has unrelated responsibilities or reasons to change; line count alone is not a defect.

```python
# SMELL: User absorbs unrelated authentication, billing, and delivery work.
class User:
    def login(self): ...
    def reset_password(self): ...
    def send_email(self): ...
    def charge_card(self): ...


# Separate by reason to change, without creating empty forwarding layers.
class User:
    def change_email(self, email: Email): ...


class AuthenticationService:
    def reset_password(self, user: User): ...


class BillingService:
    def charge(self, user: User, amount: Money): ...
```

### 3. Feature Envy

**Symptom:** Method uses another class's data more than its own.

```python
# SMELL: Order interrogates Customer's internal location fields.
class Order:
    def shipping_cost(self, customer: Customer) -> Money:
        if customer.country == "US" and customer.state == "CA":
            return Money(10, "USD")
        return Money(15, "USD")


# Move the decision to its actual information owner or a shipping policy.
class Customer:
    def shipping_region(self) -> ShippingRegion:
        return ShippingRegion(self.country, self.state)


class ShippingPolicy:
    def cost_for(self, region: ShippingRegion) -> Money: ...
```

### 4. Primitive Obsession

**Symptom:** Using primitives for domain concepts.

```python
# SMELL: unrelated primitives can be interchanged or skip validation.
def create_user(email: str, age: int, zip_code: str) -> User: ...


@dataclass(frozen=True)
class Email:
    value: str

    def __post_init__(self) -> None:
        if "@" not in self.value:
            raise InvalidEmail()


def create_user(email: Email, age: Age, address: Address) -> User: ...
```

### 5. Repeated Type Dispatch

**Symptom:** Switching on type, repeated across codebase.

```python
# SMELL: the same type dispatch appears for every new operation.
def area(shape: ShapeData) -> float:
    if shape.kind is ShapeKind.CIRCLE:
        return math.pi * shape.radius**2
    if shape.kind is ShapeKind.RECTANGLE:
        return shape.width * shape.height
    raise UnknownShape()


# If behavior truly varies by shape, let each shape own it.
class Shape(Protocol):
    def area(self) -> float: ...
    def perimeter(self) -> float: ...


class Circle:
    def __init__(self, radius: float) -> None:
        self.radius = radius

    def area(self) -> float:
        return math.pi * self.radius**2

    def perimeter(self) -> float:
        return 2 * math.pi * self.radius
```

### 6. Inappropriate Intimacy

**Symptom:** Classes know too much about each other's internals.

```python
# SMELL: Order reaches into inventory internals and mutates stock itself.
class Order:
    def process(self, inventory: Inventory) -> None:
        for item in self.items:
            stock = inventory.stock_levels[item.sku]
            if stock.quantity < item.quantity:
                raise OutOfStock()
            stock.quantity -= item.quantity


# Inventory owns reservation rules and the mutation.
class Inventory:
    def reserve(self, items: list[OrderItem]) -> None:
        if not self.can_reserve(items):
            raise OutOfStock()
        self.deduct(items)


class Order:
    def process(self, inventory: Inventory) -> None:
        inventory.reserve(self.items)
```

### 7. Speculative Generality

**Symptom:** "Just in case" abstractions that aren't used.

```python
# SMELL: hypothetical methods force unsupported implementations.
class PaymentProcessor(Protocol):
    def process(self, payment: Payment) -> None: ...
    def rollback(self) -> None: ...
    def audit(self) -> None: ...
    def schedule_recurring(self) -> None: ...


# Start with the operation the current caller needs.
class PaymentProcessor(Protocol):
    def process(self, payment: Payment) -> None: ...
```

---

## Prevention Strategies

1. **Use Object Calisthenics as review prompts** - Apply only changes that clarify responsibilities and preserve contracts
2. **Use tests for feedback** - Tests reveal design problems early; use a test-first workflow when requested without depending on another installed skill
3. **Review in pairs** - Fresh eyes catch smells
4. **Refactor continuously** - Don't let smells accumulate
5. **Apply SOLID** - Prevents structural smells
6. **Use static analysis** - Tools catch common issues

---

## When You Find a Smell

1. **Confirm it's a problem** - Not all smells need fixing
2. **Ensure test coverage** - Before refactoring
3. **Refactor in small steps** - Keep tests passing
4. **Commit frequently** - Easy to revert if needed
