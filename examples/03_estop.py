"""03 — Emergency stop: trigger, callback, inspect, reset.

Shows:
    · registering an async callback that fires on e_stop
    · triggering e_stop (which transitions session to ESTOPPED)
    · why motion commands fail after e_stop
    · how to reset and continue

Usage:
    FF_SDK_DRY_RUN=1 python 03_estop.py
"""
from __future__ import annotations

import asyncio
import logging

import ff_sdk
from ff_sdk import Config, SessionState
from ff_sdk.core.estop import EStopEvent
from ff_sdk.core.exceptions import EStopActiveError


async def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    sess = await ff_sdk.connect("D1-DEMO", config=Config(dry_run=True))

    # Register a callback — fires on any e_stop trigger
    async def on_stop(event: EStopEvent) -> None:
        print(f"  → callback fired: reason='{event.reason}' source='{event.source}'")

    unregister = sess.estop.register(on_stop)

    try:
        print(f"Session state:  {sess.session_state.value}")
        print(f"e_stop active:  {sess.estop.is_active}\n")

        print("Trigger e_stop...")
        await sess.e_stop(reason="obstacle detected", source="safety-watcher")

        print(f"\nSession state:  {sess.session_state.value}")
        print(f"e_stop active:  {sess.estop.is_active}")
        print(f"last_event:     {sess.estop.last_event}\n")

        # motion commands refuse while estop is active
        print("Try motion.stand() while estopped...")
        try:
            await sess.motion.stand()
        except EStopActiveError as e:
            print(f"  ✓ rejected as expected: {e}")

        # Reset (in production, only after operator verification)
        print("\nReset e_stop (operator confirmed)...")
        sess.estop.reset()
        # Session state doesn't auto-recover — caller decides lifecycle
        print(f"e_stop active:  {sess.estop.is_active}")
    finally:
        unregister()
        await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
