# Clean Code Practices

> Parts of this guide are based on concepts from the **"Clean Code" course summary** by [Academind GmbH / Maximilian Schwarzmuller](https://academind.com) (c) 2020.

## What is Clean Code?

Code that is:
- **Easy to understand** - reveals intent clearly
- **Easy to change** - modifications are localized
- **Easy to test** - dependencies are injectable
- **Simple** - no unnecessary complexity

## The Human-Centered Approach

Code has THREE consumers:
1. **Users** - get their needs met
2. **Customers** - make or save money
3. **Developers** - must maintain it

Design for all three, but remember: **developers read code 10x more than they write it.**

## Naming Principles

### 1. Consistency & Uniqueness (HIGHEST PRIORITY)
Same concept = same name everywhere. One name per concept.

```python
# BAD: three names for the same operation.
get_user_by_id(user_id)
fetch_customer_by_id(user_id)
retrieve_client_by_id(user_id)

# GOOD: consistent vocabulary within the domain.
get_user(user_id)
get_order(order_id)
get_product(product_id)
```

### 2. Understandability
Use domain language, not technical jargon.

```python
# BAD: technical shape hides the purpose.
arr = [user for user in users if user.is_active]

# GOOD: domain language reveals the result.
active_customers = [user for user in users if user.is_active]
```

### 3. Specificity
Avoid vague names: `data`, `info`, `manager`, `handler`, `processor`, `utils`

```python
# BAD: vague roles.
class DataManager: ...


def process_info(data): ...


# GOOD: specific responsibilities.
class OrderRepository: ...


def validate_payment(payment: Payment) -> None: ...
```

### 4. Brevity (but not at cost of clarity)
Short names are good only if meaning is preserved.

```python
# BAD: cryptic or unnecessarily long.
usr_lst = get_usrs()
list_of_all_active_users_in_the_system = get_active_users()

# GOOD: brief and clear.
active_users = get_active_users()
```

### 5. Searchability
Names should be unique enough to grep/search.

```python
# BAD: hard to search.
data = fetch()

# GOOD: names the result and operation.
order_summary = fetch_order_summary()
```

### 6. Pronounceability
You should be able to say it in conversation.

```python
# BAD: hard to pronounce.
genymdhms = generate_year_month_day_hour_minute_second()

# GOOD:
timestamp = generate_timestamp()
```

### 7. Austerity
Avoid unnecessary filler words.

```python
# BAD: filler words.
user_data = user


class UserClass: ...


# GOOD:
current_user = user


class User: ...
```

---

## Functions & Methods

Functions are the building blocks of any codebase. Clean functions are **small, focused, and readable**.

### Minimize Parameters

The fewer parameters, the easier to read and call.

```python
# Clear intent, few inputs.
create_session()
user.save()
is_valid(email)
file.write(data)

# Numerous positional values are hard to interpret.
create_user("Max", "Max", "max@example.com", "secret", 31, ["Sports"])
```

**Reduce parameters by grouping into objects:**

```python
# BAD: positional values are easy to mix up.
create_rectangle(10, 9, 30, 12)

# GOOD: named fields communicate meaning.
create_rectangle(Rectangle(x=10, y=9, width=30, height=12))
```

### Keep Functions Small

Small functions are easier to read and force you to extract well-named helper functions.

```python
# BAD: validation, lookup, authentication, and session creation mixed.
def login(email: str, password: str) -> Session:
    if "@" not in email or len(password) < 7:
        raise InvalidInput()
    user = database.find_by_email(email)
    if user is None or not user.matches_password(password):
        raise InvalidCredentials()
    return create_session(user)


# GOOD: named operations expose distinct responsibilities.
def login(email: Email, password: str) -> Session:
    user = user_repository.find_by_email(email)
    user.verify_password(password)
    return session_service.create_for(user)
```

### Do One Thing (Levels of Abstraction)

Functions should do **one thing** at **one level of abstraction**.

All operations in the function body should be on the **same level** of abstraction, exactly **one level below** the function name.

```python
# BAD: file-system options obscure the purpose.
def print_document(path: Path) -> None:
    config = {"mode": "read", "on_error": "retry"}
    document = file_system.read(path, config)
    printer = Printer("pdf")
    printer.print(document)


# GOOD: operations read at one level.
def print_document(path: Path) -> None:
    document = read_document(path)
    printer = Printer("pdf")
    printer.print(document)
```

### Split Functions Reasonably

Don't extract just for the sake of extraction. **Three warning signs** of bad splits:
1. You're just **renaming the operation** (e.g., `build_user()` that only calls `User()`)
2. You need to **scroll more** to follow a simple function
3. You **can't find a good name** because the original name is already taken

```python
# OVER-SPLIT: build_user just renames a constructor call.
def create_user(email: Email, password: str) -> User:
    validate_input(email, password)
    user = build_user(email, password)
    save_user(user)
    return user


def build_user(email: Email, password: str) -> User:
    return User(email, password)


# BETTER: keep the obvious construction visible.
def create_user(email: Email, password: str) -> User:
    validate_input(email, password)
    user = User(email, password)
    repository.add(user)
    return user
```

**Two rules of thumb for when to split:**
1. Extract code that works on the **same functionality** / is closely related
2. Extract code that requires **more interpretation** than the surrounding code

### Avoid Unexpected Side Effects

A side effect is unexpected when the function name doesn't imply it.

```python
# BAD: a validation function unexpectedly creates a session.
def validate_user_input(email: Email, password: str) -> None:
    if not password:
        raise InvalidInput()
    create_session()


# GOOD: separate or name the side effect.
def validate_and_start_session(email: Email, password: str) -> Session:
    validate_user_input(email, password)
    return create_session()
```

---

## Control Structures

Control structures (`if`, `for`, `while`, `match`) can lead to suboptimal code. Three areas to focus on:

### Prefer Positive Checks

Use positive wording in conditions when possible.

```python
# Directly name the condition being handled.
if is_empty(blog_content):
    raise EmptyContent()

# A double negative takes more effort to read.
if not has_content(blog_content):
    raise EmptyContent()
```

Exception: sometimes a negative check is cleaner when there are multiple valid states.

```python
# A negative guard can be clearer when several states are allowed.
if not transaction.is_open():
    raise ClosedTransaction()
```

### Avoid Deep Nesting

Deeply nested code is hard to read and error-prone. Strategies to flatten:

#### 1. Use Guards & Fail Fast

```python
# BAD: each nested branch adds context to keep in mind.
def message_user(user: User | None, message: str | None) -> None:
    if user is not None:
        if message:
            if user.accepts_messages:
                user.send_message(message)


# GOOD: guards leave the main action visible.
def message_user(user: User | None, message: str | None) -> None:
    if user is None or not message or not user.accepts_messages:
        return
    user.send_message(message)
```

#### 2. Extract Control Structures into Functions

```python
# BAD: connection and fallback branches obscure each other.
def connect_database(uri: str) -> Connection:
    if not uri:
        raise MissingURI()
    db = Database(uri)
    if db.connect():
        return db.connection
    if db.fallback_connection is not None:
        return db.fallback_connection
    raise ConnectionFailed()


# GOOD: the fallback has its own named policy.
def connect_database(uri: str) -> Connection:
    validate_uri(uri)
    db = Database(uri)
    if db.connect():
        return db.connection
    return connect_fallback_database(db)
```

#### 3. Use Polymorphism & Factory Functions

Replace duplicated conditional logic with polymorphic objects.

```python
# BAD: every payment method and action multiplies branching.
def process_transaction(transaction: Transaction) -> None:
    if transaction.kind is TransactionKind.PAYMENT:
        if transaction.method is PaymentMethod.CARD:
            process_card_payment(transaction)
        elif transaction.method is PaymentMethod.PAYPAL:
            process_paypal_payment(transaction)
    else:
        if transaction.method is PaymentMethod.CARD:
            process_card_refund(transaction)
        elif transaction.method is PaymentMethod.PAYPAL:
            process_paypal_refund(transaction)


# GOOD when method-specific behavior genuinely varies.
class TransactionProcessor(Protocol):
    def pay(self, transaction: Transaction) -> None: ...
    def refund(self, transaction: Transaction) -> None: ...


def process_transaction(
    transaction: Transaction, processor: TransactionProcessor
) -> None:
    if transaction.kind is TransactionKind.PAYMENT:
        processor.pay(transaction)
    else:
        processor.refund(transaction)
```

#### 4. Embrace Exceptions (`raise` instead of error codes)

```python
# BAD: synthetic codes escape into every caller.
def create_user(payload: UserCreate) -> int:
    validity = validate_input(payload)
    if validity.code in {1, 2}:
        return validity.code
    return 0


# GOOD: a specific failure crosses the boundary; FastAPI maps it to HTTP.
def create_user(payload: UserCreate) -> User:
    validate_input(payload)
    user = User.from_payload(payload)
    repository.add(user)
    return user


@app.exception_handler(EmailAlreadyUsed)
async def email_conflict(request: Request, exc: EmailAlreadyUsed) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})
```

---

## Classes & Objects

### Objects vs Data Containers

Distinguish between **objects** (hide data, expose behavior) and **data containers** (expose data, no behavior).

```python
# Data container: the HTTP boundary exposes fields intentionally.
class UserResponse(BaseModel):
    name: str
    age: int


# Domain object: behavior maintains its own invariants.
class User:
    def __init__(self, name: str, age: int) -> None:
        self._name = name
        self._age = age

    def greeting(self) -> str:
        return f"Hi! I'm {self._name} and I'm {self._age} years old."
```

Both are valid - choose based on responsibility. A domain object owns behavior that protects its invariants; a data container intentionally carries data across a boundary. A persistence-facing object can serve either role, so do not infer its role from how it is stored. Do not add domain behavior to a type whose responsibility is only to represent or transfer data, and do not keep domain rules outside an object that owns them merely because the object also participates in persistence.

### Class Cohesion

**Cohesion** = how much methods use class properties. High cohesion means every method uses most properties.

- **High cohesion** → well-designed, focused class
- **Low cohesion** → should probably be a data container or split into smaller classes

When cohesion decreases, **split into smaller, more focused classes**.

---

## Object Calisthenics (9 Rules)

Exercises to improve OO design. In production, use these as review prompts rather than mandatory transformations; preserve the actual contract and avoid changes made only to satisfy the exercise. Pydantic request/response models and SQLAlchemy rows are data containers, not behavior-rich domain objects.

### 1. Review Deeply Nested Control Flow

```python
# BAD: nested decisions across orders and items.
def process(orders: list[Order]) -> None:
    for order in orders:
        if order.is_valid():
            for item in order.items:
                if item.in_stock:
                    process_item(item)


# GOOD: name the responsibility at each level.
def process(orders: list[Order]) -> None:
    for order in orders:
        if order.is_valid():
            process_order(order)


def process_order(order: Order) -> None:
    for item in order.in_stock_items():
        process_item(item)
```

### 2. Prefer Clear Control Flow

Use early returns, guard clauses, or polymorphism when they improve clarity. An else branch is valid when it expresses the decision clearly.

```python
# BAD: needless else after a return.
def discount(user: User) -> int:
    if user.is_premium:
        return 20
    else:
        return 0


# GOOD: direct return for the remaining case.
def discount(user: User) -> int:
    if user.is_premium:
        return 20
    return 0
```

### 3. Wrap Values with Domain Meaning or Invariants

Primitives should be wrapped in domain objects when they have meaning.

```python
# BAD: domain constraints can be skipped by passing raw primitives.
def create_user(email: str, age: int) -> User: ...


@dataclass(frozen=True)
class Age:
    value: int

    def __post_init__(self) -> None:
        if not 0 <= self.value <= 150:
            raise InvalidAge()


def create_user(email: Email, age: Age) -> User: ...
```

### 4. First-Class Collections

Consider a first-class collection when it owns meaningful rules. A collection does not impose a limit on the containing object's other fields.

```python
# A collection with its own rules can own those operations.
class OrderItems:
    def __init__(self, items: list[OrderItem] | None = None) -> None:
        self._items = list(items or [])

    def add(self, item: OrderItem) -> None:
        self._items.append(item)

    def total(self) -> Money:
        return sum_money(item.total() for item in self._items)


class Order:
    def __init__(self, customer_id: CustomerId, items: OrderItems) -> None:
        self.customer_id = customer_id
        self.items = items
```

### 5. Review Traversal of Object Internals

Don't chain through object graphs.

```python
# BAD for a behavior-rich object: traversal exposes its representation.
city = order.customer.address.city

# GOOD: order supplies the information needed by its caller.
city = order.shipping_city()
```

### 6. Don't Abbreviate

If a name is too long to type, the class is doing too much.

```python
# BAD: abbreviations conceal roles.
cust_repo = CustRepo()
ord = Ord()

# GOOD:
customer_repository = CustomerRepository()
order = Order()
```

### 7. Review Size and Cohesion

Review classes, methods, and files for mixed responsibilities and difficulty of change. Line counts are signals, not limits; split only when doing so creates a clearer responsibility.

### 8. Review Whether Fields Form a Coherent Concept

Field count alone does not justify splitting a class or introducing a wrapper. Group values when they form a genuine concept.

```python
# Too many unrelated fields may signal mixed responsibilities.
class Order:
    id: OrderId
    customer_id: CustomerId
    items: list[Item]
    total: Money
    status: OrderStatus


# Group values only when they form a genuine concept.
@dataclass(frozen=True)
class OrderDetails:
    customer: Customer
    line_items: OrderItems


class Order:
    def __init__(self, order_id: OrderId, details: OrderDetails) -> None:
        self.id = order_id
        self.details = details
```

### 9. Keep Decisions with Behavior-Rich Objects

Objects should have behavior, not just data. Tell objects what to do.

```python
# BAD: caller reads the balance and performs the account's mutation.
if account.balance >= amount:
    account.balance -= amount


# GOOD: account owns both decision and state change.
class Account:
    def withdraw(self, amount: Money) -> None:
        if self._balance < amount:
            raise InsufficientFunds()
        self._balance -= amount


account.withdraw(amount)
```

---

## Comments

### When to Write Comments

**Only write comments to explain WHY, not WHAT or HOW.**

Code explains what and how. Comments explain business reasons, non-obvious decisions, or warnings.

```python
# BAD: repeats the code's action.
# Add one to the counter.
counter += 1

# GOOD: records a non-obvious reason.
# The legacy API numbers items from one, while our index starts at zero.
legacy_position = index + 1
```

### Prefer Self-Documenting Code

Instead of commenting, rename to make intent clear.

```python
# BAD: comment compensates for an opaque condition.
# Check if user can access premium features.
if user.subscription_level >= 2 and not user.is_banned:
    ...

# GOOD: a named domain decision owns the rule.
if user.can_access_premium_features():
    ...
```

---

## Formatting

### Vertical Spacing
- Related code together
- Blank lines between concepts
- Most important/public at top

### Horizontal Spacing
- Consistent indentation
- Space around operators
- Max line length ~80-120 characters

### Storytelling
Code should read top-to-bottom like a story. High-level at top, details below.

```python
class OrderProcessor:
    def process(self, order: Order) -> ProcessResult:
        self._validate(order)
        self._calculate_totals(order)
        return self._save(order)

    def _validate(self, order: Order) -> None: ...
    def _calculate_totals(self, order: Order) -> None: ...
    def _save(self, order: Order) -> ProcessResult: ...
```

---

## Clean Code Checklist

### Naming
- [ ] Use **descriptive** and meaningful names
  - Variables & Properties: Nouns or short phrases with adjectives
  - Functions & Methods: Verbs or short phrases with adjectives
  - Classes: Nouns
- [ ] Be as **specific** as necessary and possible
- [ ] Use **yes/no "questions"** for booleans (e.g. `isValid`)
- [ ] Avoid **misleading** names
- [ ] Be **consistent** with names (e.g. stick to `get...` instead of `fetch...`)

### Comments & Formatting
- [ ] **Most comments are bad** - avoid them!
- [ ] Acceptable comments: legal info, warnings, regex explanations, todos
- [ ] Use **vertical formatting**: keep related concepts close, separate unrelated ones
- [ ] Write code **top to bottom**: called functions below calling functions
- [ ] Use **horizontal formatting**: avoid long lines, use indentation for scope

### Functions
- [ ] **Limit parameters** - fewer is better, use objects to group
- [ ] Functions should be **small and do one thing**
  - Abstraction levels in the body should be **one level below** the function name
  - **Don't mix** levels of abstraction
  - But **avoid redundant splitting**!
- [ ] Stay **DRY** (Don't Repeat Yourself)
- [ ] **Avoid unexpected side effects**

### Control Structures & Errors
- [ ] Prefer **positive checks**
- [ ] Avoid **deep nesting**
  - Use **guard** statements and fail fast
  - Use **polymorphism** and factory functions
  - **Extract** control structures into separate functions
- [ ] Use **meaningful exceptions** (`raise`/`except`) instead of synthetic error codes

### Objects & Classes
- [ ] Build either **"real objects"** or **data containers** - don't mix
- [ ] Build **small classes** focused on a **single responsibility** (not "single method"!)
- [ ] Build classes with **high cohesion**
- [ ] Follow the **Law of Demeter** for real objects (avoid `order.customer.last_purchase.date`)
- [ ] Follow the **SOLID principles** (especially SRP and OCP for clean code)
