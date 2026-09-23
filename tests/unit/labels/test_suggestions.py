from uuid import uuid4

import pytest
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.models.test import TestModel

from mosemo.labels.suggestions import (
    LabelCandidate,
    PydanticAILabelSuggester,
    SuggestionInput,
)


@pytest.mark.asyncio
async def test_pydantic_ai_accepts_a_structured_active_label() -> None:
    label_id = uuid4()
    suggester = PydanticAILabelSuggester(
        TestModel(custom_output_args={"label_id": str(label_id)})
    )

    result = await suggester.suggest(
        SuggestionInput(
            activity="app_name: Editor",
            labels=(LabelCandidate(label_id, "코딩"),),
            examples=(),
        )
    )

    assert result.label_id == label_id
    assert result.provider == "test"
    assert result.prompt_version == "v1"
    assert result.input_tokens is not None


@pytest.mark.asyncio
async def test_pydantic_ai_rejects_a_label_outside_the_candidate_set() -> None:
    suggester = PydanticAILabelSuggester(
        TestModel(custom_output_args={"label_id": str(uuid4())})
    )

    with pytest.raises(UnexpectedModelBehavior):
        await suggester.suggest(
            SuggestionInput(
                activity="app_name: Editor",
                labels=(LabelCandidate(uuid4(), "코딩"),),
                examples=(),
            )
        )
