"""Unit test support for use cases: record transaction blocks without a database."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest


class TransactionRecorder:
    """Stands in for `unit_of_work`/`read_snapshot` and counts the blocks."""

    def __init__(self) -> None:
        self.entered = 0
        self.committed = 0

    @asynccontextmanager
    async def __call__(self, *_args: object) -> AsyncIterator[None]:
        self.entered += 1
        yield
        self.committed += 1


def record_transactions(
    monkeypatch: pytest.MonkeyPatch, module: str, *names: str
) -> TransactionRecorder:
    recorder = TransactionRecorder()
    for name in names:
        monkeypatch.setattr(f"{module}.{name}", recorder)
    return recorder
