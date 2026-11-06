"""E3 판정용 pytest 플러그인: `FocusSession.validate_activity`를 항상 통과하는 구현으로 바꾼다.

`run.py`가 평가 작업 공간 루트에 복사하고 `python -m pytest -p eval_e3_mutant`로
새 테스트만 실행한다. 새 테스트가 1개 이상 실패해야 경계 사례를 검증한 것이다.
"""

from datetime import datetime
from uuid import UUID

from mosemo.observation.domain.focus_sessions import FocusSession


def always_valid(
    self: FocusSession, *, account_id: UUID, device_id: UUID, observed_at: datetime
) -> None:
    del self, account_id, device_id, observed_at


def pytest_configure() -> None:
    FocusSession.validate_activity = always_valid
