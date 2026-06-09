"""hello_connect — minimum-viable ff_sdk usage.

Run on any dev machine:
    export FF_SDK_DRY_RUN=1
    python examples/01_hello_connect.py --target D1-DEMO

Run with a real D1:
    # FF_SDK_DRY_RUN unset
    python examples/01_hello_connect.py --target D1-<sn>
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import ff_sdk
from ff_sdk import Config


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="D1-DEMO",
                        help="Target URI: D1-<sn>")
    args = parser.parse_args()

    cfg = Config.from_env()
    print(f"Config: dry_run={cfg.dry_run} timeout={cfg.transport_timeout}s")

    session = await ff_sdk.connect(args.target, config=cfg)
    try:
        print(f"\nConnected. State: {session.session_state.value}")
        print(f"Capabilities: {session.capabilities() or '(none in Phase 1)'}")
        print("\nDiagnostics:")
        print(session.diagnose().summary())

        print("\nTriggering e_stop (manual test)...")
        await session.e_stop(reason="hello_connect demo")
        print(f"State after e_stop: {session.session_state.value}")
    finally:
        await session.close()
        print(f"\nClosed. Session uptime: {session.uptime:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
