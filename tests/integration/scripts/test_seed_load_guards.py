from types import ModuleType

import pytest

from tests.support.seed_database import SeedDatabase


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(
            ("--users", "1", "--end-date", "2026-01-31", "--confirm-host", "other"),
            id="host-mismatch",
        ),
        pytest.param(
            ("--users", "0", "--end-date", "2026-01-31", "--confirm-host", "{host}"),
            id="zero-users",
        ),
        pytest.param(("--users", "1", "--confirm-host", "{host}"), id="no-end-date"),
        pytest.param(
            ("--users", "1", "--end-date", "2026-31-01", "--confirm-host", "{host}"),
            id="bad-end-date",
        ),
    ],
)
def test_guard_and_argument_errors_exit_2_without_writes(
    seed_database: SeedDatabase,
    seed_load: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
) -> None:
    before = seed_database.counts()

    def no_connection(*_args, **_kwargs):
        raise AssertionError("the guard must stop before connecting")

    monkeypatch.setattr(seed_load, "create_async_engine", no_connection)

    exit_code = seed_database.run(
        *(arg.format(host=seed_database.host) for arg in args)
    )

    assert exit_code == 2
    assert seed_database.counts() == before
