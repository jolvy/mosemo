"""Framework-free activity registration message and terminal diagnostic boundary."""

from dataclasses import dataclass
from uuid import UUID


class TerminalBatchError(Exception):
    """Safe diagnostic code only; never include source text or tracebacks."""


@dataclass(frozen=True, slots=True)
class ActivityRegistrationRequested:
    account_id: UUID
    device_id: UUID
    batch_id: UUID
    body: bytes
