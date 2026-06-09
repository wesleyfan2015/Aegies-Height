"""cookbook/graceful_shutdown — Ctrl+C 安全停止。

信号处理：收到 SIGINT/SIGTERM 后
  1. 立即触发 e_stop（硬件先停）
  2. 取消主循环任务
  3. 关闭会话

不优雅的关退（kill -9 / 裸 exit）在真机上会留电机带电，必须避免。

运行：
    FF_SDK_DRY_RUN=1 python cookbook/graceful_shutdown.py
    # 按 Ctrl+C 看效果
"""
from __future__ import annotations

import asyncio
import signal
import sys

import ff_sdk
from ff_sdk import Config


async def main_loop(sess: ff_sdk.Session) -> None:
    tick = 0
    while True:
        tick += 1
        status = await sess.state.status()
        print(f"[{tick:03d}] status={status.value}  (Ctrl+C to stop)")
        await asyncio.sleep(1.0)


async def main() -> None:
    sess = await ff_sdk.connect("D1-DEMO", config=Config.from_env())
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _on_signal(signame: str) -> None:
        print(f"\n[{signame}] graceful shutdown initiated")
        stop_event.set()

    # Windows doesn't support loop.add_signal_handler — fall back to default Ctrl+C
    for signame in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _on_signal, signame)
        except NotImplementedError:
            signal.signal(sig, lambda s, f: _on_signal(signame))

    worker = asyncio.create_task(main_loop(sess))
    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        print(">> triggering e_stop (hardware halt)")
        await sess.e_stop(reason="user requested shutdown")
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        await sess.close()
        print(">> session closed cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
