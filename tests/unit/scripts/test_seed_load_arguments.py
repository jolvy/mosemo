from types import ModuleType

import pytest

REQUIRED = ["--users", "1", "--confirm-host", "localhost", "--end-date", "2026-11-07"]


def test_profile_defaults_to_heavy_and_accepts_measured(
    seed_load: ModuleType,
) -> None:
    parser = seed_load._parser()

    assert parser.parse_args(REQUIRED).profile == "heavy"
    assert parser.parse_args([*REQUIRED, "--profile", "measured"]).profile == (
        "measured"
    )


def test_unknown_profile_exits_2_before_connecting(
    seed_load: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(
        seed_load, "create_async_engine", lambda *args, **kwargs: calls.append(args)
    )

    with pytest.raises(SystemExit) as exited:
        seed_load.main([*REQUIRED, "--profile", "other"])

    assert exited.value.code == 2
    assert calls == []
