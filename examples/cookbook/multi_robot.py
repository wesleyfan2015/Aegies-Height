"""cookbook/multi_robot — 并发控制多台机器人。

`asyncio.gather` 让多个 Session 完全并发。每台机器人的命令互不阻塞。

运行：
    FF_SDK_DRY_RUN=1 python cookbook/multi_robot.py
"""
from __future__ import annotations

import asyncio

import ff_sdk
from ff_sdk import Config


async def greet_routine(target: str, name: str, cfg: Config) -> None:
    """A simple per-robot routine."""
    sess = await ff_sdk.connect(target, config=cfg)
    async with sess:
        print(f"[{name}] connected")
        await sess.motion.stand()
        await asyncio.sleep(0.2)

        bat = await sess.state.battery()
        print(f"[{name}] battery reading: {bat.percent}")

        await sess.motion.do_preset("damping")
        print(f"[{name}] done")


async def main() -> None:
    cfg = Config(dry_run=True)

    # Fleet: two Aegis quadrupeds
    fleet = [
        ("D1-DEMO", "Rover-1"),
        ("D1-DEMO-2", "Rover-2"),
    ]

    # Fire all robots concurrently
    tasks = [greet_routine(t, n, cfg) for t, n in fleet]
    await asyncio.gather(*tasks)

    print(f"\nAll {len(fleet)} robots completed their routines concurrently.")


if __name__ == "__main__":
    asyncio.run(main())
