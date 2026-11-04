class UseCase:
    """Base of one use case whose only public method is `async def execute`.

    Each subclass defines its own `execute` signature (ADR 0013).
    """
