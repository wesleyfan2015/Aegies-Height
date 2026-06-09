"""D1 cmd_vel test that does not call motion.stop() or damping().

On the current AEGIS EDU test robot, the SDK example's motion.stop() appears
to put the robot into damping/safe-halt. This script tests velocity control
while avoiding stop()/damping(); it sends repeated zero-velocity commands to
stop walking instead.

Safety:
    Put the robot in a clear area. Start with the robot already standing.

Usage on the Pi:
    FF_SDK_D1_HOST=192.168.234.1 FF_SDK_D1_VARIANT=zsl-1 \
    python examples/d1/cmd_vel_no_damping_test.py
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os

import ff_sdk
from ff_sdk import Config


async def send_velocity_for(sess, *, linear: float, angular: float, lateral: float, seconds: float) -> None:
    """Send velocity repeatedly so the robot receives a live command stream."""

    interval = 0.10
    loops = max(1, int(seconds / interval))
    for _ in range(loops):
        await sess.motion.cmd_vel(linear=linear, angular=angular, lateral=lateral)
        await asyncio.sleep(interval)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="D1-DEMO")
    parser.add_argument("--host", default=os.environ.get("FF_SDK_D1_HOST", "192.168.234.1"))
    parser.add_argument("--variant", default=os.environ.get("FF_SDK_D1_VARIANT", "zsl-1"))
    parser.add_argument("--speed", type=float, default=0.08)
    parser.add_argument("--seconds", type=float, default=0.8)
    parser.add_argument("--stand-first", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    os.environ["FF_SDK_D1_HOST"] = args.host
    os.environ["FF_SDK_D1_VARIANT"] = args.variant

    config = Config.from_env()
    config.extra["d1_host"] = args.host
    config.extra["d1_variant"] = args.variant

    print("mode=cmd_vel_no_damping_test")
    print(f"target={args.target}")
    print(f"FF_SDK_D1_HOST={args.host}")
    print(f"FF_SDK_D1_VARIANT={args.variant}")
    print(f"speed_mps={args.speed}")
    print(f"duration_seconds={args.seconds}")
    print("final_stop_method=cmd_vel_zero_only")

    sess = await ff_sdk.connect(args.target, config=config)
    try:
        if args.stand_first:
            print(">> stand")
            await sess.motion.stand()
            await asyncio.sleep(2.0)
        else:
            print(">> stand skipped; robot should already be standing")

        print(">> forward velocity stream")
        await send_velocity_for(
            sess,
            linear=args.speed,
            angular=0.0,
            lateral=0.0,
            seconds=args.seconds,
        )

        print(">> zero velocity stream")
        await send_velocity_for(
            sess,
            linear=0.0,
            angular=0.0,
            lateral=0.0,
            seconds=1.0,
        )

        status = await sess.state.status()
        print(f"status_after_zero_velocity={status.value}")
        print("done_no_stop_no_damping")
    finally:
        await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
