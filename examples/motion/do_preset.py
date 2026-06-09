"""motion/do_preset — preset actions (站立 / 趴下 / 特技).

`do_preset(name)` executes a named built-in action. Unknown preset names
raise `CapabilityNotSupported` with the list of known names — never
silent fallback.

Usage:
    FF_SDK_DRY_RUN=1 python motion/do_preset.py
    python motion/do_preset.py --target D1-DEMO --preset stand
    python motion/do_preset.py --target D1-DEMO --preset jump
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import ff_sdk
from ff_sdk import CapabilityNotSupported, Config


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="D1-DEMO")
    p.add_argument("--preset", default="stand",
                   help="preset name (try 'stand' / 'shake_hand' / 'damping' / 'jump')")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO)

    sess = await ff_sdk.connect(args.target, config=Config.from_env())
    try:
        print(f">> do_preset('{args.preset}') on {sess.adapter.platform_name}")
        try:
            r = await sess.motion.do_preset(args.preset)
            print(f"   ✓ success: {r.message or 'ok'}")
            if r.details:
                print(f"   details: {r.details}")
        except CapabilityNotSupported as e:
            print(f"   ✗ not supported on this robot: {e}")
    finally:
        await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
