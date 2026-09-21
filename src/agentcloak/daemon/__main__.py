"""Entry point for background daemon: python -m agentcloak.daemon."""

import argparse
import asyncio

from agentcloak.daemon.server import start


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default=None,
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--headless", action="store_true", help="Force headless mode.")
    parser.add_argument("--headed", action="store_true", help="Force headed mode.")
    parser.add_argument("--profile", default=None, help="Browser profile name.")
    parser.add_argument(
        "--humanize",
        action="store_true",
        help="Enable humanize behavioral layer.",
    )
    parser.add_argument(
        "--no-humanize",
        action="store_true",
        help="Explicitly disable humanize layer.",
    )
    args = parser.parse_args()
    humanize: bool | None = None
    if args.humanize:
        humanize = True
    elif args.no_humanize:
        humanize = False
    asyncio.run(
        start(
            host=args.host,
            port=args.port,
            headless=True if args.headless else (False if args.headed else None),
            profile=args.profile,
            humanize=humanize,
            log_level=args.log_level,
        )
    )


main()
