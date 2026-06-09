"""02 — Diagnose a robot before doing anything else.

Always diagnose first when onboarding a new machine or troubleshooting.
Usage:
    FF_SDK_DRY_RUN=1 python 02_diagnose.py
    python 02_diagnose.py --target D1-DEMO
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import ff_sdk
from ff_sdk import Config


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="D1-DEMO")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO)

    sess = await ff_sdk.connect(args.target, config=Config.from_env())
    try:
        report = sess.diagnose()
        print("\n─── Diagnostic Report ───────────────────────────")
        print(report.summary())
        print("─────────────────────────────────────────────────")
        print(f"\nPlatform:  {report.platform}")
        print(f"Target:    {report.target}")
        print(f"Overall:   {report.overall.value}")
        print(f"Checks:    {len(report.checks)}")
        for c in report.checks:
            print(f"  · {c.name}: {c.status.value} — {c.detail}")
        print(f"\nSupported capabilities: {sorted(sess.capabilities())}")
    finally:
        await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
