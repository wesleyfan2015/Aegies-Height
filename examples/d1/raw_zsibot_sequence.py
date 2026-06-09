"""Run a verified D1 movement sequence through the raw zsibot backend.

Sequence:
    stand_up -> zero warmup -> forward -> back -> left -> right -> zero stop

Usage:
    python examples/d1/raw_zsibot_sequence.py
"""
from __future__ import annotations

import argparse
import time

from ff_sdk.internal.oem.zsibot import ZsibotClient, detect_local_ip


def stream_move(
    dog: ZsibotClient,
    *,
    label: str,
    vx: float,
    vy: float,
    yaw: float,
    seconds: float,
) -> int | None:
    print(f">> {label}: vx={vx:.2f}, vy={vy:.2f}, yaw={yaw:.2f}, seconds={seconds:.1f}")
    end = time.monotonic() + max(0.0, seconds)
    last_ret = None
    while time.monotonic() < end:
        last_ret = dog.move(vx, vy, yaw)
        time.sleep(0.05)
    print(f"{label}_ret={last_ret}")
    print(f"{label}_mode={dog.ctrl_mode()}")
    print(f"{label}_position={dog.position()}")
    print(f"{label}_velocity={dog.world_velocity()}")
    return last_ret


def zero_velocity(dog: ZsibotClient, seconds: float) -> int | None:
    return stream_move(
        dog,
        label="zero",
        vx=0.0,
        vy=0.0,
        yaw=0.0,
        seconds=seconds,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.168.234.1")
    parser.add_argument("--variant", default="zsl-1")
    parser.add_argument("--stand-wait", type=float, default=3.0)
    parser.add_argument("--warmup-seconds", type=float, default=1.0)
    parser.add_argument("--move-seconds", type=float, default=1.2)
    parser.add_argument("--zero-seconds", type=float, default=1.0)
    parser.add_argument("--forward-speed", type=float, default=0.35)
    parser.add_argument("--back-speed", type=float, default=0.25)
    parser.add_argument("--lateral-speed", type=float, default=0.18)
    args = parser.parse_args()

    local_ip = detect_local_ip(args.host)
    moves = [
        ("forward", args.forward_speed, 0.0, 0.0),
        ("back", -args.back_speed, 0.0, 0.0),
        ("left", 0.0, args.lateral_speed, 0.0),
        ("right", 0.0, -args.lateral_speed, 0.0),
    ]

    dog = ZsibotClient(
        dog_ip=args.host,
        local_ip=local_ip,
        local_port=43988,
        variant=args.variant,
    )
    try:
        print("sequence=stand_forward_back_left_right")
        print(f"robot_host={args.host}")
        print(f"local_ip={local_ip}")
        print(f"variant={args.variant}")
        connected = dog.connect(settle_timeout=5.0)
        print(f"connected={connected}")
        if not connected:
            raise RuntimeError("zsibot backend did not connect")

        print(f"battery={dog.battery()}")
        print(f"initial_mode={dog.ctrl_mode()}")

        print(">> stand_up")
        print(f"stand_up_ret={dog.stand_up()}")
        time.sleep(args.stand_wait)
        print(f"mode_after_stand={dog.ctrl_mode()}")

        print(">> zero warmup")
        zero_velocity(dog, args.warmup_seconds)

        for label, vx, vy, yaw in moves:
            stream_move(
                dog,
                label=label,
                vx=vx,
                vy=vy,
                yaw=yaw,
                seconds=args.move_seconds,
            )
            zero_velocity(dog, args.zero_seconds)

        print(f"final_mode={dog.ctrl_mode()}")
        print(f"final_position={dog.position()}")
        print(f"final_velocity={dog.world_velocity()}")
    finally:
        dog.close()


if __name__ == "__main__":
    main()
