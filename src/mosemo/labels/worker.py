import argparse
import asyncio

from mosemo.config import get_config
from mosemo.database import SessionFactory, engine
from mosemo.labels.proposals import ProposalProcessor, ProposalScanner
from mosemo.labels.suggestions import PydanticAILabelSuggester


async def run(*, once: bool, poll_seconds: float) -> None:
    model = get_config().label_model
    if not model:
        raise SystemExit("LABEL_MODEL is required to run the label proposal worker")
    processor = ProposalProcessor(
        SessionFactory, suggester=PydanticAILabelSuggester(model)
    )
    scanner = ProposalScanner(SessionFactory, processor)
    try:
        if once:
            await scanner.run_once()
            return
        while True:
            await scanner.run_batch()
            await asyncio.sleep(poll_seconds)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate pending activity labels")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    asyncio.run(run(once=args.once, poll_seconds=args.poll_seconds))


if __name__ == "__main__":
    main()
