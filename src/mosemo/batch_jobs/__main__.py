"""Batch job process: python -m mosemo.batch_jobs {serve | run JOB_NAME}."""

import argparse
import asyncio
import signal
import sys


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m mosemo.batch_jobs", description="Run batch jobs"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Run scheduled jobs until stopped")
    run_command = commands.add_parser("run", help="Run one job once and exit")
    run_command.add_argument("job_name")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    from mosemo.logging import configure_logging

    configure_logging()
    # Configuration and engine initialization must also stay inside main's
    # protected boundary: validation errors can contain credential input values.
    from mosemo.batch_jobs.job import run_job_once
    from mosemo.batch_jobs.registry import build_jobs
    from mosemo.batch_jobs.server import serve
    from mosemo.clock import utc_now
    from mosemo.config import get_config
    from mosemo.database import SessionFactory, engine

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    for sig in signals:
        loop.add_signal_handler(sig, stop.set)
    try:
        jobs = build_jobs(SessionFactory, get_config())
        if args.command == "serve":
            await serve(jobs, stop, clock=utc_now)
            return 0
        job = next((job for job in jobs if job.name == args.job_name), None)
        if job is None:
            names = ", ".join(job.name for job in jobs)
            print(f"Unknown batch job; available jobs: {names}", file=sys.stderr)
            return 2
        return 0 if await run_job_once(job, stop) else 1
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)
        await engine.dispose()


def main() -> None:
    args = parse_args()
    loop = asyncio.new_event_loop()
    try:
        exit_code = loop.run_until_complete(run(args))
    except Exception, asyncio.CancelledError:  # noqa: BLE001 - safe process boundary
        # Do not print configuration input values, SQL parameters or secrets.
        raise SystemExit("Batch job process failed") from None
    finally:
        # Unlike asyncio.run, do not cancel and await leftover tasks: a job that
        # ignores cancellation must not keep the process past the shutdown deadline.
        # Such jobs were already logged; silence their pending-task reports, which
        # would otherwise fire while the interpreter tears down logging.
        loop.set_exception_handler(lambda loop, context: None)
        loop.close()
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
