# Managing Complexity

## The Two Types of Complexity

### Essential Complexity
Inherent to the problem domain. Cannot be removed, only managed.
- Business rules
- Domain logic
- User requirements

### Accidental Complexity
Introduced by our solutions. CAN and SHOULD be minimized.
- Poor abstractions
- Unnecessary indirection
- Framework ceremony
- Technical debt

**Goal: Minimize accidental complexity while clearly expressing essential complexity.**

---

## Detecting Complexity

### 1. Change Amplification
Small changes require touching many files.

**Symptom:** "To add this field, I need to update 15 files."

**Cause:** Scattered responsibilities, poor abstraction boundaries.

### 2. Cognitive Load
Code is hard to understand, requires holding too much in memory.

**Symptom:** "I need to understand 10 other classes to understand this one."

**Cause:** Tight coupling, hidden dependencies, unclear naming.

### 3. Unknown Unknowns
Behavior is surprising, side effects are hidden.

**Symptom:** "I changed this, and something completely unrelated broke."

**Cause:** Global state, hidden dependencies, implicit contracts.

---

## The XP Values for Fighting Complexity

From Extreme Programming:

### 1. Communication
Code should communicate clearly. Names, structure, tests all contribute.

### 2. Simplicity
Do the simplest thing that could possibly work.

### 3. Feedback
Fast feedback loops catch complexity early: tests, CI, and code review. Use a test-first workflow when requested; no separately installed skill is required.

### 4. Courage
Refactor aggressively. Don't let complexity accumulate.

### 5. Respect
Respect future readers (including yourself). Write for humans first.

---

## KISS - Keep It Simple, Silly

> "The simplest solution that works is usually the best."

### How to Apply:
1. Start with the obvious solution
2. Only add complexity when REQUIRED
3. Prefer boring, well-understood approaches
4. Question every abstraction

```python
# Over-engineered for one implementation:
class UserServiceFactoryProvider:
    def create_factory(self) -> UserServiceFactory: ...


# KISS:
class UserService:
    def get_user(self, user_id: str) -> User: ...
```

---

## YAGNI - You Aren't Gonna Need It

> "Don't build features until they're actually needed."

### Warning Signs:
- "We might need this later"
- "It would be nice to have"
- "Just in case"
- "For future extensibility"

### The Cost of YAGNI Violations:
1. **Development time** - Building unused features
2. **Maintenance burden** - Code that must be maintained
3. **Cognitive load** - More to understand
4. **Wrong abstraction** - Guessing future needs incorrectly

```python
# YAGNI violation: fields added for hypothetical needs
class User(BaseModel):
    name: str
    email: EmailStr
    secondary_email: EmailStr | None = None
    fax_number: str | None = None
    linkedin_profile: str | None = None


# Only the current contract:
class User(BaseModel):
    name: str
    email: EmailStr
```

---

## DRY - Don't Repeat Yourself (with The Rule of Three)

> "Every piece of knowledge should have a single, unambiguous representation."

### BUT: The Rule of Three

**Don't extract duplication until you see it THREE times.**

Why? The wrong abstraction is worse than duplication.

```
Duplication #1 → Leave it
Duplication #2 → Note it, leave it
Duplication #3 → NOW extract it
```

### Example:
```python
# First use: keep the flow visible.
def process_user_order(order: Order) -> None:
    validate(order)
    calculate_tax(order)
    save(order)


# Second use: note the similarity, including the difference.
def process_guest_order(order: Order) -> None:
    validate(order)
    calculate_tax(order)
    save(order)
    send_guest_email(order)


# When the same rule genuinely recurs, extract it.
def process_order(order: Order, after_save: Callable[[Order], None]) -> None:
    validate(order)
    calculate_tax(order)
    save(order)
    after_save(order)
```

---

## Separation of Concerns

> "Each module should address a single concern."

### Concerns to Separate:
- **Business logic** vs **Infrastructure**
- **What** (policy) vs **How** (mechanism)
- **Input** vs **Processing** vs **Output**
- **Data** vs **Behavior**

### Example:
```python
# Mixed concerns: domain rule, SQL, and notification in one method.
class OrderProcessor:
    def process(self, order: Order) -> None:
        if not order.items:
            raise EmptyOrder()
        total = sum(item.price * item.quantity for item in order.items)
        self.session.add(OrderRow.from_order(order, total))
        self.email_client.send(order.customer.email, "Order confirmed")


# Collaborators own their rules; the processor coordinates the workflow.
class OrderProcessor:
    def __init__(self, repository: OrderRepository, notifier: OrderNotifier):
        self.repository = repository
        self.notifier = notifier

    def process(self, order: Order) -> None:
        order.validate_and_calculate_total()
        self.repository.save(order)
        self.notifier.confirm_order(order)
```

---

## Managing Technical Debt

### Types of Technical Debt:
1. **Deliberate** - Conscious trade-off for speed
2. **Accidental** - Mistakes, lack of knowledge
3. **Bit rot** - Code degrades over time

### The Boy Scout Rule:
> "Leave the code better than you found it."

Every time you touch code:
- Improve one small thing
- Fix one naming issue
- Extract one method
- Add one missing test

### When to Pay Down Debt:
- When it's in your path (you're already there)
- When it's blocking new features
- When it's causing bugs
- During dedicated refactoring time

### When NOT to Refactor:
- Code that works and won't change
- Code being replaced soon
- When you don't have tests

---

## The Four Elements of Simple Design

In priority order (from XP):

1. **Runs all the tests**
   - If it doesn't work, nothing else matters

2. **Expresses intent**
   - Clear names, obvious structure
   - Code tells the story

3. **No duplication**
   - DRY (but Rule of Three)
   - Single source of truth

4. **Minimal**
   - Fewest classes and methods possible
   - Remove anything unnecessary

If these four are true, the design is simple enough.
