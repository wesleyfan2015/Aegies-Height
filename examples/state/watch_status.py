"""state/watch_status — poll status continuously for N seconds.

Simple telemetry watcher — useful during manual testing / demos.

Usage:
    FF_SDK_DRY_RUN=1 python state/watch_status.py
    python state/watch_status.py --target D1-DEMO --duration 10
"""
from __future__ import annotations

import argparse
import asyncio
import time

import ff_sdk
from ff_sdk import Config


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="D1-DEMO")
    p.add_argument("--duration", type=float, default=5.0, help="seconds")
    p.add_argument("--interval", type=float, default=0.5, help="poll interval")
    args = p.parse_args()

    sess = await ff_sdk.connect(args.target, config=Config.from_env())
    try:
        deadline = time.time() + args.duration
        last = None
        while time.time() < deadline:
            status = await sess.state.status()
            if status != last:
                print(f"[{time.strftime('%H:%M:%S')}] status: {status.value}")
                last = status
            await asyncio.sleep(args.interval)
        print(f"\nWatched {args.duration}s, {sess.adapter.platform_name}")
    finally:
        await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
