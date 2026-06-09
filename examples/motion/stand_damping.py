"""motion/stand_damping — stand up, hold, then damping.

Demonstrates the stand ↔ damping state transition. After damping the
robot is in a zero-torque / safe-halt state; commanding motion requires
a fresh stand.

Usage:
    FF_SDK_DRY_RUN=1 python motion/stand_damping.py
    python motion/stand_damping.py --target D1-DEMO
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import ff_sdk
from ff_sdk import Config


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="D1-DEMO")
    p.add_argument("--hold", type=float, default=2.0, help="seconds to hold stand")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO)
    sess = await ff_sdk.connect(args.target, config=Config.from_env())
    try:
        print(">> Stand up")
        r = await sess.motion.stand()
        print(f"   {r.message or 'ok'}")

        s = await sess.state.status()
        print(f"   status: {s.value}")

        print(f">> Holding {args.hold:.1f}s")
        await asyncio.sleep(args.hold)

        print(">> Damping (safe halt)")
        r = await sess.motion.damping()
        print(f"   {r.message or 'ok'}")

        s = await sess.state.status()
        print(f"   status: {s.value}")
    finally:
        await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
