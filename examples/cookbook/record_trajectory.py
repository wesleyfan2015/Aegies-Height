"""cookbook/record_trajectory — 轮询 pose 写 CSV。

用途：把一次真机运行的运动轨迹导出，便于事后回放或做数据收集。

运行：
    FF_SDK_DRY_RUN=1 python cookbook/record_trajectory.py
    python cookbook/record_trajectory.py --target D1-DEMO --duration 3 --out traj.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import time

import ff_sdk
from ff_sdk import Config


async def record(sess: ff_sdk.Session, *, duration: float, rate: float,
                 out_path: str) -> int:
    period = 1.0 / rate
    rows: list[list[float]] = []
    t0 = time.time()
    deadline = t0 + duration
    while time.time() < deadline:
        ts = time.time() - t0
        pose = await sess.state.pose()
        bat = await sess.state.battery()
        rows.append([ts, pose.x, pose.y, pose.z,
                     pose.roll, pose.pitch, pose.yaw,
                     bat.percent])
        await asyncio.sleep(period)

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "x", "y", "z", "roll", "pitch", "yaw", "battery"])
        w.writerows(rows)
    return len(rows)


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="D1-DEMO")
    p.add_argument("--duration", type=float, default=2.0, help="seconds")
    p.add_argument("--rate", type=float, default=20.0, help="Hz")
    p.add_argument("--out", default="trajectory.csv")
    args = p.parse_args()

    sess = await ff_sdk.connect(args.target, config=Config.from_env())
    async with sess:
        n = await record(sess, duration=args.duration, rate=args.rate,
                         out_path=args.out)
    print(f"Wrote {n} rows to {args.out} "
          f"({args.rate:.0f} Hz × {args.duration:.1f} s)")


if __name__ == "__main__":
    asyncio.run(main())
