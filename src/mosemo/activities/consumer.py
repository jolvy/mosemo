"""Separate activity Consumer process: python -m mosemo.activities.consumer."""

import asyncio
import signal


async def run() -> None:
    # Configuration and engine initialization must also stay inside main's
    # protected boundary: validation errors can contain credential input values.
    from mosemo.config import get_config
    from mosemo.database import SessionFactory, engine
    from mosemo.infra.batch_runtime import MessagingRuntime

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    for sig in signals:
        loop.add_signal_handler(sig, stop.set)
    try:
        await MessagingRuntime.run_consumer(
            get_config().batch_delivery, SessionFactory, stop
        )
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)
        await engine.dispose()


def main() -> None:
    try:
        asyncio.run(run())
    except Exception:  # noqa: BLE001 - process boundary suppresses sensitive exception data
        # Do not print payloads, Pydantic input values, SQL parameters or secrets.
        raise SystemExit(
            "Activity Consumer failed; failure/recovery policy is not implemented"
        ) from None


if __name__ == "__main__":
    main()
