"""cookbook/diagnose_report — 把诊断结果导成结构化 JSON（CI 可用）。

多台机器人的 diagnose 结果写到同一个 report.json，便于机群健康仪表盘
或 CI 校验上线前体检。

运行：
    FF_SDK_DRY_RUN=1 python cookbook/diagnose_report.py --out report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import ff_sdk
from ff_sdk import Config


async def diagnose_target(target: str, cfg: Config) -> dict:
    try:
        sess = await ff_sdk.connect(target, config=cfg)
    except Exception as e:
        return {
            "target": target,
            "connect_error": f"{type(e).__name__}: {e}",
            "overall": "fail",
        }
    async with sess:
        report = sess.diagnose()
        return {
            "target": report.target,
            "platform": report.platform,
            "overall": report.overall.value,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "detail": c.detail,
                    "latency_ms": c.latency_ms,
                }
                for c in report.checks
            ],
            "capabilities": sorted(sess.capabilities()),
        }


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="report.json")
    p.add_argument("--targets", nargs="+",
                   default=["D1-DEMO", "D1-DEMO-2"])
    args = p.parse_args()

    cfg = Config.from_env()
    results = await asyncio.gather(*[diagnose_target(t, cfg) for t in args.targets])
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "robots": results,
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r.get("overall") == "ok"),
            "warn": sum(1 for r in results if r.get("overall") == "warn"),
            "fail": sum(1 for r in results if r.get("overall") == "fail"),
        },
    }
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps(report["summary"], indent=2))
    print(f"→ {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
