"""Pure parts of the daily stats batch: settings, wait points, run report."""

import asyncio
from collections import Counter

import pytest

from mosemo.analytics.application.daily_stats_batch import (
    NO_WAIT_POINTS,
    WAIT_POINT_NAMES,
    RefreshReport,
    RefreshSettings,
    WaitPoints,
)
from mosemo.analytics.bootstrap import AnalyticsBatchConfig
from mosemo.platform.logging import OPTIONAL_FIELDS


def test_wait_points_are_off_by_default() -> None:
    settings = AnalyticsBatchConfig(reprocess_per_run=600).settings()

    assert settings.wait_points == NO_WAIT_POINTS
    assert settings.wait_points.enabled is False
    assert RefreshSettings(reprocess_per_run=1).wait_points.enabled is False


@pytest.mark.asyncio
async def test_disabled_wait_point_returns_at_once(tmp_path) -> None:
    wait_points = WaitPoints(names=frozenset({"after_claim"}), directory=tmp_path)

    await asyncio.wait_for(wait_points.reach("after_source_read"), 1)
    await asyncio.wait_for(NO_WAIT_POINTS.reach("after_claim"), 1)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_enabled_wait_point_waits_for_the_go_signal(tmp_path) -> None:
    wait_points = WaitPoints(names=WAIT_POINT_NAMES, directory=tmp_path)
    waiting = asyncio.create_task(wait_points.reach("after_claim"))
    while not (tmp_path / "after_claim.reached").exists():
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert not waiting.done()

    (tmp_path / "after_claim.go").touch()
    await asyncio.wait_for(waiting, 1)

    assert not (tmp_path / "after_claim.go").exists()


def test_wait_points_need_known_names_and_a_directory(tmp_path) -> None:
    with pytest.raises(ValueError, match="unknown"):
        WaitPoints(names=frozenset({"nowhere"}), directory=tmp_path)
    with pytest.raises(ValueError, match="directory"):
        WaitPoints(names=frozenset({"after_claim"}))


def test_settings_reject_a_negative_reprocess_cap() -> None:
    with pytest.raises(ValueError, match="negative"):
        RefreshSettings(reprocess_per_run=-1)


def test_report_fields_are_logged_scalars() -> None:
    report = RefreshReport(saved=2, failures=Counter({"compute": 1, "lock_timeout": 2}))

    fields = report.fields()

    assert set(fields) <= set(OPTIONAL_FIELDS)
    assert (fields["failed"], fields["failed_compute"]) == (3, 1)
    assert fields["failed_lock_timeout"] == 2
    assert fields["failed_statement_timeout"] == 0
    assert report.failed is False
    assert RefreshReport(aborted="connection").failed is True
    assert RefreshReport(aborted="definition").failed is False
