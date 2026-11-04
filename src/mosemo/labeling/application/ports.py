from typing import Protocol

from mosemo.labeling.domain.suggestions import SuggestionInput, SuggestionResult


class LabelSuggester(Protocol):
    """The AI label suggester; implemented by `PydanticAILabelSuggester`.

    Called outside any transaction. Any exception counts as a failed attempt.
    """

    async def suggest(self, request: SuggestionInput) -> SuggestionResult: ...
