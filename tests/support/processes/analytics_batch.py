"""Run the real batch job entrypoint with test-only faults switched by env.

- `TEST_ANALYTICS_COMPUTE_FAULT` = `raise` or `kill` on the date
  `TEST_ANALYTICS_FAULT_DATE` (ISO): the calculation raises, or the process
  kills itself with SIGKILL there.
- `TEST_ANALYTICS_DEFINITION_VERSION`: the code definition version.
- `TEST_ANALYTICS_KILL_AFTER_ACCOUNTS` = n: the definition mark kills the
  process with SIGKILL inside the transaction of account n + 1.

Everything else (settings, wait points, logging, exit codes) is the real code.
"""

import os
import runpy
import signal
import sys
from dataclasses import replace
from datetime import date
from unittest.mock import patch

from mosemo.analytics import bootstrap as analytics_bootstrap
from mosemo.analytics.domain.daily_stats import DailyStatsCalculator
from mosemo.analytics.domain.markers import MarkScope
from mosemo.analytics.infrastructure.markers import RecomputeMarkerRepository

original_compute = DailyStatsCalculator.compute
original_upsert = RecomputeMarkerRepository.upsert_definition
marked_accounts: list[object] = []


def faulty_compute(self, snapshot, window):
    fault = os.environ.get("TEST_ANALYTICS_COMPUTE_FAULT")
    day = date.fromisoformat(os.environ.get("TEST_ANALYTICS_FAULT_DATE", "1970-01-01"))
    if fault and MarkScope.date_of(snapshot.timezone, window.start) == day:
        if fault == "kill":
            os.kill(os.getpid(), signal.SIGKILL)
        raise ArithmeticError("calculation failed")
    return original_compute(self, snapshot, window)


async def killing_upsert(self, db_session, *, account_id, **values):
    limit = int(os.environ["TEST_ANALYTICS_KILL_AFTER_ACCOUNTS"])
    if account_id not in marked_accounts:
        marked_accounts.append(account_id)
    await original_upsert(self, db_session, account_id=account_id, **values)
    if len(marked_accounts) > limit:
        print("MARK_KILLED_INSIDE_ACCOUNT", flush=True)
        os.kill(os.getpid(), signal.SIGKILL)


if __name__ == "__main__":
    patches = [patch.object(DailyStatsCalculator, "compute", faulty_compute)]
    version = os.environ.get("TEST_ANALYTICS_DEFINITION_VERSION")
    if version:
        patches.append(
            patch.object(
                analytics_bootstrap,
                "CURRENT_DEFINITION",
                replace(analytics_bootstrap.CURRENT_DEFINITION, version=int(version)),
            )
        )
    if os.environ.get("TEST_ANALYTICS_KILL_AFTER_ACCOUNTS"):
        patches.append(
            patch.object(RecomputeMarkerRepository, "upsert_definition", killing_upsert)
        )
    for active in patches:
        active.start()
    sys.argv = ["mosemo.entrypoints.batch", *sys.argv[1:]]
    runpy.run_module("mosemo.entrypoints.batch", run_name="__main__")
