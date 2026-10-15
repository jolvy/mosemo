# Object-Oriented Design

## Responsibility-Driven Design (RDD)

The key insight: **Objects are defined by their responsibilities, not their data.**

### Finding Objects

Start with:
1. **Nouns** in requirements → candidate objects
2. **Verbs** → candidate methods/behaviors
3. **Domain concepts** → value objects

### Finding Responsibilities

Each object should answer:
- What does this object **know**?
- What does this object **do**?
- What does this object **decide**?

### Object Stereotypes

Every class fits one (or maybe two) stereotypes:

| Stereotype | Purpose | Example |
|------------|---------|---------|
| **Information Holder** | Knows things, holds data | `User`, `Product`, `Address` |
| **Structurer** | Maintains relationships | `OrderItems`, `UserGroup` |
| **Service Provider** | Performs work | `PaymentProcessor`, `EmailSender` |
| **Coordinator** | Orchestrates workflow | `OrderFulfillmentService` |
| **Controller** | Makes decisions, delegates | `CheckoutController` |
| **Interfacer** | Transforms between systems | `UserAPIAdapter`, `DatabaseMapper` |

### The Two Questions

For every class, ask:
1. **"What pattern is this?"** - Which stereotype? Which design pattern?
2. **"Is it doing too much?"** - Check object calisthenics rules

If you can't answer clearly, the class needs refactoring.

---

## Tell, Don't Ask

**Command objects to do work. Don't interrogate them and do the work yourself.**

```python
# BAD: caller knows the balance rule and mutates the account directly.
if account.balance >= amount:
    account.balance -= amount

# GOOD: the account owns the decision and the state change.
account.withdraw(amount)
```

The object that has the data should have the behavior.

When a decision depends on several domain objects, place it in a collaborating domain object or domain operation that receives the required objects. Keep each participant's local invariants and state changes with that participant. The caller may coordinate loading, persistence, transactions, external work, and operation order without taking ownership of the domain decision. Do not place the rule in one object merely because it has the most data.

---

## Design by Contract (DbC)

Every method has:
- **Preconditions** - What must be true BEFORE calling
- **Postconditions** - What will be true AFTER calling
- **Invariants** - What is ALWAYS true about the object

```python
class BankAccount:
    def __init__(self, balance: Money) -> None:
        self._balance = balance

    def withdraw(self, amount: Money) -> Money:
        if amount <= Money.zero(amount.currency):
            raise InvalidAmount()
        if self._balance < amount:
            raise InsufficientFunds()
        self._balance = self._balance - amount
        return self._balance
```

---

## Composition Over Inheritance

**Prefer composing objects over extending classes.**

### Why Inheritance is Problematic:
- Tight coupling between parent and child
- Fragile base class problem
- Difficult to change parent without breaking children
- Forces "is-a" relationship that may not fit

### When to Use Inheritance:
- True "is-a" relationship (rare)
- Framework requirements
- Template Method pattern (intentional)

### Prefer Composition:
```python
# Inheritance binds premium behavior to the User type.
class PremiumUser(User):
    def discount(self) -> Decimal:
        return Decimal("0.20")


# Composition makes the policy explicit and replaceable.
class User:
    def __init__(self, discount_policy: DiscountPolicy) -> None:
        self.discount_policy = discount_policy

    def discount(self) -> Decimal:
        return self.discount_policy.calculate()


user = User(PremiumDiscount())
```

---

## The Law of Demeter (Principle of Least Knowledge)

**Only talk to your immediate friends.**

A method should only call:
1. Methods on `this`
2. Methods on parameters
3. Methods on objects it creates
4. Methods on its direct components

```python
# BAD: the caller traverses several internal relationships.
city = order.customer.address.city

# GOOD: ask for the information the caller actually needs.
city = order.shipping_city()
```

This reduces coupling - changes to `Address` don't ripple through all callers.

---

## Encapsulation

**Hide internal details, expose behavior.**

### Levels of Encapsulation:
1. **Data** - private fields, no direct access
2. **Implementation** - how things work internally
3. **Type** - concrete class hidden behind interface
4. **Design** - architectural decisions hidden from clients

```python
# BAD: callers can bypass the total invariant.
class Order:
    items: list[OrderItem]
    total: Money


order.items.append(item)
order.total = Money(-999, "USD")


# GOOD: one operation preserves the invariant.
class Order:
    def add_item(self, item: OrderItem) -> None:
        self._items.append(item)
        self._total = sum_items(self._items)

    @property
    def total(self) -> Money:
        return self._total
```

---

## Polymorphism

**Replace conditionals with types.**

```python
# Repeated shipping type checks make each new type a scattered edit.
class ShippingMethod(Protocol):
    def cost(self, order_value: Money) -> Money: ...


class StandardShipping:
    def cost(self, order_value: Money) -> Money:
        return Money(0, "USD") if order_value.amount >= 50 else Money(5, "USD")


class ExpressShipping:
    def cost(self, order_value: Money) -> Money:
        return Money(15, "USD")


def shipping_cost(method: ShippingMethod, order_value: Money) -> Money:
    return method.cost(order_value)
```

---

## Value Objects vs Entities

### Value Objects
- Defined by their attributes (no identity)
- Immutable
- Comparable by value
- Examples: `Money`, `Email`, `Address`, `DateRange`

```python
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def add(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise CurrencyMismatch()
        return Money(self.amount + other.amount, self.currency)
```

### Entities
- Have identity (survives attribute changes)
- Usually mutable (via methods)
- Comparable by identity
- Examples: `User`, `Order`, `Product`

```python
class User:
    def __init__(self, user_id: UserId, email: Email) -> None:
        self.id = user_id
        self._email = email

    def change_email(self, new_email: Email) -> None:
        self._email = new_email  # the identity stays the same

    def same_identity_as(self, other: "User") -> bool:
        return self.id == other.id
```

---

## Aggregates

A cluster of objects treated as a single unit for data changes.

- One object is the **aggregate root** (entry point)
- External code only references the root
- Root enforces invariants for the entire cluster

```python
class Order:
    def __init__(self) -> None:
        self._items: list[OrderItem] = []

    def add_item(self, product: Product, quantity: int) -> None:
        item = OrderItem(product, quantity)
        self._items.append(item)
        self._validate_total()

    def remove_item(self, item_id: ItemId) -> None:
        self._items = [item for item in self._items if item.id != item_id]

    def _validate_total(self) -> None:
        if self.total() > MAX_ORDER_VALUE:
            raise OrderTotalExceeded()


order.add_item(product, 2)  # callers cannot bypass the root's check
```
