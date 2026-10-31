# SOLID Principles

## Overview

SOLID helps structure software to be flexible, maintainable, and testable. These principles reduce coupling and increase cohesion.

## S - Single Responsibility Principle (SRP)

> "A class should have one, and only one, reason to change."

### Problem It Solves
God objects that do everything - hard to test, hard to change, hard to understand.

### How to Apply
Each class handles ONE responsibility. If you find yourself saying "and" when describing what a class does, split it.

```python
# BAD: domain, persistence, and presentation change for different reasons.
class Order:
    def calculate_total(self) -> Decimal: ...
    def save_to_database(self) -> None: ...
    def generate_invoice_pdf(self) -> bytes: ...


# GOOD: collaborators own their respective responsibilities.
class Order:
    def add_item(self, item: OrderItem) -> None: ...
    def calculate_total(self) -> Decimal: ...


class OrderRepository:
    def add(self, order: Order) -> None: ...


class InvoiceRenderer:
    def render(self, order: Order) -> bytes: ...
```

### Detection Questions
- Does this class have multiple reasons to change?
- Can I describe it without using "and"?
- Would different stakeholders request changes to different parts?

---

## O - Open/Closed Principle (OCP)

> "Software entities should be open for extension but closed for modification."

### Problem It Solves
Having to modify existing, tested code every time requirements change. Risk of breaking working features.

### How to Apply
When a variation is real, design an extension point so adding a new behavior does not scatter edits through existing callers.

```python
from typing import Protocol


# Repeated type checks across the codebase are a change amplifier.
class ShippingMethod(Protocol):
    def cost(self, order_value: Decimal) -> Decimal: ...


class StandardShipping:
    def cost(self, order_value: Decimal) -> Decimal:
        return Decimal(0) if order_value >= 50 else Decimal(5)


class ExpressShipping:
    def cost(self, order_value: Decimal) -> Decimal:
        return Decimal(15)


# A new policy implements the same small contract.
class SameDayShipping:
    def cost(self, order_value: Decimal) -> Decimal:
        return Decimal(25)
```

### Architectural Insight
OCP at architecture level means: **design your codebase so new features are added by adding code, not changing existing code.**

---

## L - Liskov Substitution Principle (LSP)

> "Subtypes must be substitutable for their base types without altering program correctness."

### Problem It Solves
Subclasses that break expectations, requiring type-checking and special cases.

### How to Apply
Subclasses must honor the contract of the parent. If the parent returns positive numbers, subclasses cannot return negatives.

```python
class DiscountPolicy(Protocol):
    def discount(self, subtotal: Decimal) -> Decimal: ...  # 0 <= result <= subtotal


class BrokenDiscount:
    def discount(self, subtotal: Decimal) -> Decimal:
        return Decimal(-5)  # breaks the caller's contract


class FixedDiscount:
    def __init__(self, amount: Decimal) -> None:
        if amount < 0:
            raise ValueError("discount must be non-negative")
        self.amount = amount

    def discount(self, subtotal: Decimal) -> Decimal:
        return min(self.amount, subtotal)
```

### Key Insight
This is why you can swap `InMemoryUserRepo` for `PostgresUserRepo` - they both honor the `UserRepo` interface contract.

---

## I - Interface Segregation Principle (ISP)

> "Clients should not be forced to depend on methods they do not use."

### Problem It Solves
Fat interfaces that force partial implementations, empty methods, or throws.

### How to Apply
Split large interfaces into smaller, cohesive ones. Clients depend only on what they need.

```python
class WarehouseDevice(Protocol):  # Too broad for a simple printer
    def print_label(self, order_id: str) -> None: ...
    def scan_barcode(self) -> str: ...
    def package_item(self, order_id: str) -> None: ...


class LabelPrinter(Protocol):
    def print_label(self, order_id: str) -> None: ...


class BasicPrinter:
    def print_label(
        self, order_id: str
    ) -> None: ...  # no unrelated methods to implement
```

### Detection
Repeated `raise NotImplementedError` or unused methods can indicate a contract that is too broad.

---

## D - Dependency Inversion Principle (DIP)

> "High-level modules should not depend on low-level modules. Both should depend on abstractions."

### Problem It Solves
Tight coupling to specific implementations (databases, APIs, frameworks). Hard to test, hard to swap.

### How to Apply
Depend on interfaces, inject implementations.

```python
class EmailSender(Protocol):
    async def send(self, to: str, message: str) -> None: ...


class OrderService:
    def __init__(self, email_sender: EmailSender) -> None:
        self.email_sender = email_sender

    async def confirm_order(self, email: str) -> None:
        await self.email_sender.send(email, "Order confirmed")


# FastAPI can supply the chosen implementation at the HTTP boundary.
def get_order_service() -> OrderService:
    return OrderService(email_sender=SendGridEmailSender())
```

### The Dependency Rule
Source code dependencies should point **inward** toward high-level policies (domain logic), never toward low-level details (infrastructure).

```
Infrastructure → Application → Domain
      ↑              ↑            ↑
    (outer)       (middle)     (inner)

Dependencies flow: outer → inner
Never: inner → outer
```

---

## Applying SOLID at Architecture Level

These principles scale beyond classes:

| Principle | Architecture Application |
|-----------|--------------------------|
| SRP | Each bounded context has one responsibility |
| OCP | New features = new modules, not edits to existing |
| LSP | Microservices with same contract are substitutable |
| ISP | Thin interfaces between services |
| DIP | High-level business logic doesn't know about databases/frameworks |

---

## Quick Reference

| Principle | One-Liner | Red Flag |
|-----------|-----------|----------|
| SRP | One reason to change | "This class handles X and Y and Z" |
| OCP | Add, don't modify | `if/else` chains for types |
| LSP | Subtypes are substitutable | Type-checking in calling code |
| ISP | Small, focused interfaces | Empty method implementations |
| DIP | Depend on the needed contract | Constructing an external SDK inside domain logic |
