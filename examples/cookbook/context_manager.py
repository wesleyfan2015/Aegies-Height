"""cookbook/context_manager — async-with 自动连/关。

比手工 try/finally 更稳。Session 支持 `async with` 协议，进入时 open，退出时
close（即使因异常退出也会 close）。

运行：
    FF_SDK_DRY_RUN=1 python cookbook/context_manager.py
"""
from __future__ import annotations

import asyncio

import ff_sdk
from ff_sdk import Config, Identity


async def main() -> None:
    cfg = Config.from_env()
    ident = Identity.anonymous()

    # 方式 A：顶层 connect() + async with Session
    sess = await ff_sdk.connect("D1-DEMO", config=cfg, identity=ident)
    async with sess:
        print(f"state inside: {sess.session_state.value}")
        await sess.motion.stand()
    print(f"state after with: {sess.session_state.value}")  # DISCONNECTED

    # 方式 B：try/finally 等价写法
    sess2 = await ff_sdk.connect("D1-DEMO-2", config=cfg)
    try:
        bat = await sess2.state.battery()
        print(f"battery: {bat.percent * 100:.0f}%")
    finally:
        await sess2.close()


if __name__ == "__main__":
    asyncio.run(main())
