"""state/read_battery — read the robot battery level.

Usage:
    FF_SDK_DRY_RUN=1 python state/read_battery.py
    python state/read_battery.py --target D1-DEMO
"""
from __future__ import annotations

import argparse
import asyncio
import math

import ff_sdk
from ff_sdk import Config


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="D1-DEMO")
    args = p.parse_args()

    sess = await ff_sdk.connect(args.target, config=Config.from_env())
    try:
        bat = await sess.state.battery()
        if math.isnan(bat.percent):
            print(f"Battery: unknown (platform has no battery telemetry yet)")
        else:
            bar_len = 20
            filled = int(bat.percent * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"Battery: [{bar}] {bat.percent * 100:.0f}%"
                  + (f"  {bat.voltage:.1f}V" if bat.voltage else "")
                  + ("  ⚡ charging" if bat.is_charging else ""))
    finally:
        await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
