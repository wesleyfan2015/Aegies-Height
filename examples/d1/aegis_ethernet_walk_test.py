"""Safe AEGIS/D1 Ethernet walking test.

Use this when the Pi is connected to the robot by Ethernet or LAN and you know
the robot IP address. The script prints the exact SDK host/variant, stands the
robot, moves forward briefly, stops, moves backward briefly, stops, then damps.

Usage on the Pi:
    python examples/d1/aegis_ethernet_walk_test.py --host <robot-ip>

For the normal robot hotspot, the host is usually 192.168.234.1:
    python examples/d1/aegis_ethernet_walk_test.py --host 192.168.234.1
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os

import ff_sdk
from ff_sdk import Config


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="D1-DEMO")
    parser.add_argument(
        "--host",
        default=os.environ.get("FF_SDK_D1_HOST", "192.168.234.1"),
        help="Robot IP address for Ethernet/LAN/hotspot mode.",
    )
    parser.add_argument(
        "--variant",
        default=os.environ.get("FF_SDK_D1_VARIANT", "zsl-1"),
        help="D1 variant. AEGIS EDU/Ultra footed robots use zsl-1.",
    )
    parser.add_argument("--speed", type=float, default=0.15)
    parser.add_argument("--seconds", type=float, default=1.0)
    parser.add_argument("--stand-seconds", type=float, default=2.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    os.environ["FF_SDK_D1_HOST"] = args.host
    os.environ["FF_SDK_D1_VARIANT"] = args.variant

    config = Config.from_env()
    config.extra["d1_host"] = args.host
    config.extra["d1_variant"] = args.variant

    print("mode=aegis_ethernet_walk_test")
    print(f"target={args.target}")
    print(f"FF_SDK_D1_HOST={args.host}")
    print(f"FF_SDK_D1_VARIANT={args.variant}")
    print(f"speed_mps={args.speed}")
    print(f"duration_seconds={args.seconds}")
    print("SAFETY: clear the area and keep the controller/kill switch ready.")

    sess = await ff_sdk.connect(args.target, config=config)
    try:
        print(">> stand")
        await sess.motion.stand()
        await asyncio.sleep(args.stand_seconds)

        print(">> forward")
        await sess.motion.cmd_vel(linear=args.speed, angular=0.0, lateral=0.0)
        await asyncio.sleep(args.seconds)

        print(">> stop")
        await sess.motion.stop()
        await asyncio.sleep(0.7)

        print(">> backward")
        await sess.motion.cmd_vel(linear=-args.speed, angular=0.0, lateral=0.0)
        await asyncio.sleep(args.seconds)

        print(">> stop")
        await sess.motion.stop()
        await asyncio.sleep(0.7)

        print(">> damping")
        await sess.motion.damping()
    finally:
        await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
