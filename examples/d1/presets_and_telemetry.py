"""d1/presets_and_telemetry — Aegis 机型选择 + 特技动作 + 关节遥测.

演示三件事:
  1. 用 variant 参数适配不同机型 (点足 zsl-1 / 轮足 zsl-1w, 含 EDU / Ultra)
  2. 调用特技 preset 并按建议时长等待动作完成
  3. 读取关节遥测 (做了能力探测, 不支持的机型/固件优雅降级)

Usage:
    FF_SDK_DRY_RUN=1 python d1/presets_and_telemetry.py            # 干跑, 任何电脑
    python d1/presets_and_telemetry.py                             # 轮足 (默认)
    python d1/presets_and_telemetry.py --variant zsl-1             # 点足 / EDU / Ultra
    python d1/presets_and_telemetry.py --variant zsl-1 --trick shake_hand

Safety: 特技动作前确认机器人四周空旷、电量充足。
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import ff_sdk
from ff_sdk import Config
from ff_sdk.core.exceptions import CapabilityNotSupported


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--variant", default=None, choices=["zsl-1", "zsl-1w"],
                   help="机型: zsl-1=点足(EDU/Ultra/XG01) | zsl-1w=轮足(默认)")
    p.add_argument("--trick", default="shake_hand",
                   help="要演示的特技 preset 名 (默认 shake_hand)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO)

    # ── 1. 机型适配: variant 通过 Config.extra 传入 ──────────────────
    cfg = Config.from_env()
    if args.variant:
        cfg.extra["d1_variant"] = args.variant

    dog = await ff_sdk.connect("D1-DEMO", config=cfg)
    try:
        # 体检: 看运动后端 / 遥测链路是否就绪
        print(dog.diagnose().summary())

        # 当前机型支持的全部 preset
        presets = sorted(dog.motion.known_presets())
        print(f"\n本机型支持的 preset ({len(presets)}): {', '.join(presets)}")

        # ── 2. 站立 → 特技 → 趴下, 每步等够建议时长 ──────────────────
        for name in ("stand", args.trick, "lie_down", "damping"):
            if name not in presets:
                print(f">> 跳过 '{name}' (本机型不支持)")
                continue
            wait = dog.motion.preset_timeout(name)
            print(f">> do_preset('{name}')  预计 {wait:.0f}s")
            result = await dog.motion.do_preset(name)
            print(f"   {'✓' if result.success else '✗'} {result.message}")
            await asyncio.sleep(wait)

        # ── 3. 遥测: 电量 / 状态 / 位姿 / 关节 ──────────────────────
        battery = await dog.state.battery()
        status = await dog.state.status()
        pose = await dog.state.pose()
        print(f"\n电量: {battery.percent * 100:.0f}%   状态: {status.value}")
        print(f"位姿: x={pose.x:.2f} y={pose.y:.2f} yaw={pose.yaw:.2f}")

        # 关节遥测: 点足 12 关节 / 轮足 16 关节; 部分轮足固件不支持 → 优雅降级
        try:
            joints = await dog.state.joint_states()
            print(f"关节 ({len(joints.names)}):")
            for n, q in zip(joints.names, joints.positions):
                print(f"  {n:12s} {q:+.3f} rad")
        except CapabilityNotSupported as e:
            print(f"关节遥测不可用 (本机型/固件限制): {e}")
    finally:
        await dog.close()


if __name__ == "__main__":
    asyncio.run(main())
