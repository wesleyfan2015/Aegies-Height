"""d1/udp_walk — Aegis quadruped full walking demo.

Flow: handshake → stand → walk forward 2s → yaw → stop → damping.

All motion commands go via the D1 control link. Dry-run mode
logs the JSON packets instead of sending them.

Important: Aegis starts in "autonomous mode" which ignores velocity
packets — the SDK's first cmd_vel call automatically switches the robot
to "remote mode". No action needed in user code; this comment is just
FYI for debugging.

Usage:
    FF_SDK_DRY_RUN=1 python d1/udp_walk.py
    python d1/udp_walk.py                                # default: hotspot gateway
    FF_SDK_D1_HOST=<robot-ip> python d1/udp_walk.py      # Ethernet / LAN mode

Safety: put the dog on a stand or in a safe area. Keep the kill switch close.
"""
from __future__ import annotations

import asyncio
import logging

import ff_sdk
from ff_sdk import Config


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    sess = await ff_sdk.connect("D1-demo", config=Config.from_env())
    try:
        print(">> stand up")
        await sess.motion.stand()
        await asyncio.sleep(2.0)

        print(">> walk forward 0.3 m/s for 2s")
        await sess.motion.cmd_vel(linear=0.3)
        await asyncio.sleep(2.0)

        print(">> yaw in place 0.3 rad/s for 2s")
        await sess.motion.cmd_vel(linear=0.0, angular=0.3)
        await asyncio.sleep(2.0)

        print(">> stop")
        await sess.motion.stop()
        await asyncio.sleep(0.5)

        print(">> damping (safe halt)")
        await sess.motion.damping()
    finally:
        await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
