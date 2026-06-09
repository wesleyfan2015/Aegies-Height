"""motion/cmd_vel — continuous velocity control.

Drives the robot forward at 0.3 m/s for 1 second, then yaws, then stops.

Usage:
    FF_SDK_DRY_RUN=1 python motion/cmd_vel.py
    python motion/cmd_vel.py --target D1-DEMO
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import ff_sdk
from ff_sdk import Config, Twist


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="D1-DEMO")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO)

    sess = await ff_sdk.connect(args.target, config=Config.from_env())
    try:
        # Explicit keyword args
        print(">> Forward 0.3 m/s for 1.0 s")
        await sess.motion.cmd_vel(linear=0.3, angular=0.0)
        await asyncio.sleep(1.0)

        # Or pass a Twist DTO
        print(">> Yaw 0.4 rad/s for 1.0 s")
        await sess.motion.cmd_twist(Twist(linear=0.0, angular=0.4))
        await asyncio.sleep(1.0)

        print(">> Stop")
        await sess.motion.stop()

        # Verify via state
        status = await sess.state.status()
        print(f"Status after stop: {status.value}")
    finally:
        await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
