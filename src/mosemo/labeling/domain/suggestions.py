"""Values exchanged with the AI label suggester (`application/ports.py`)."""

from dataclasses import dataclass
from uuid import UUID

PROMPT_VERSION = "v1"


@dataclass(frozen=True)
class LabelCandidate:
    label_id: UUID
    display_name: str


@dataclass(frozen=True)
class ConfirmedExample:
    confirmation_id: UUID
    activity: str
    label_id: UUID | None


@dataclass(frozen=True)
class SuggestionInput:
    activity: str
    labels: tuple[LabelCandidate, ...]
    examples: tuple[ConfirmedExample, ...]


@dataclass(frozen=True)
class SuggestionResult:
    label_id: UUID | None
    provider: str
    model: str
    prompt_version: str
    input_tokens: int | None
    output_tokens: int | None
