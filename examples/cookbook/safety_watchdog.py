"""cookbook/safety_watchdog — 后台监控电量和状态，条件触发 e_stop。

典型场景：长任务中电量跌破阈值 / 状态进入 FAULT 时自动保护。
主任务跑 motion，watchdog 并行跑。

运行：
    FF_SDK_DRY_RUN=1 python cookbook/safety_watchdog.py
"""
from __future__ import annotations

import argparse
import asyncio
import math

import ff_sdk
from ff_sdk import Config, RobotStatus


async def watchdog(sess: ff_sdk.Session, *,
                   low_battery: float = 0.20,
                   poll_hz: float = 2.0) -> None:
    """Poll state; trigger e_stop if conditions violated."""
    period = 1.0 / poll_hz
    while True:
        await asyncio.sleep(period)
        if sess.estop.is_active:
            return
        bat = await sess.state.battery()
        status = await sess.state.status()

        if not math.isnan(bat.percent) and bat.percent < low_battery:
            print(f"[watchdog] battery {bat.percent * 100:.0f}% < "
                  f"{low_battery * 100:.0f}% → e_stop")
            await sess.e_stop(reason="low battery", source="watchdog")
            return
        if status is RobotStatus.FAULT:
            print(f"[watchdog] status=FAULT → e_stop")
            await sess.e_stop(reason="robot fault", source="watchdog")
            return


async def main_work(sess: ff_sdk.Session) -> None:
    """Pretend long-running task: alternating forward/yaw."""
    for i in range(20):
        if sess.estop.is_active:
            print("[main] e_stop active, exiting")
            return
        print(f"[main] iter {i}: cmd_vel forward")
        await sess.motion.cmd_vel(linear=0.3)
        await asyncio.sleep(1.0)


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="D1-DEMO")
    p.add_argument("--low-battery", type=float, default=0.20,
                   help="trigger e_stop below this battery fraction")
    args = p.parse_args()

    sess = await ff_sdk.connect(args.target, config=Config.from_env())
    async with sess:
        watcher = asyncio.create_task(
            watchdog(sess, low_battery=args.low_battery)
        )
        worker = asyncio.create_task(main_work(sess))
        await worker
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
