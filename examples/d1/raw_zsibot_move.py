"""Simple verified D1 movement commands using the raw zsibot backend.

This avoids the high-level sess.motion.cmd_vel path, which was unreliable on
the AEGIS EDU test robot. The command pattern is:

    stand_up -> wait -> zero warmup -> stream move -> zero stop

Usage:
    python examples/d1/raw_zsibot_move.py forward
    python examples/d1/raw_zsibot_move.py back
    python examples/d1/raw_zsibot_move.py left
    python examples/d1/raw_zsibot_move.py right
"""
from __future__ import annotations

import argparse
import time

from ff_sdk.internal.oem.zsibot import ZsibotClient, detect_local_ip


MOVES = {
    "forward": (0.35, 0.0, 0.0),
    "back": (-0.25, 0.0, 0.0),
    "backward": (-0.25, 0.0, 0.0),
    "left": (0.0, 0.18, 0.0),
    "right": (0.0, -0.18, 0.0),
    "yaw_left": (0.0, 0.0, 0.25),
    "yaw_right": (0.0, 0.0, -0.25),
    "zero": (0.0, 0.0, 0.0),
}


def stream_move(dog: ZsibotClient, vx: float, vy: float, yaw: float, seconds: float) -> int | None:
    end = time.monotonic() + max(0.0, seconds)
    last_ret = None
    while time.monotonic() < end:
        last_ret = dog.move(vx, vy, yaw)
        time.sleep(0.05)
    return last_ret


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("direction", choices=sorted(MOVES))
    parser.add_argument("--host", default="192.168.234.1")
    parser.add_argument("--variant", default="zsl-1")
    parser.add_argument("--seconds", type=float, default=1.2)
    parser.add_argument("--stand-wait", type=float, default=3.0)
    parser.add_argument("--warmup-seconds", type=float, default=1.0)
    parser.add_argument("--stop-seconds", type=float, default=1.0)
    args = parser.parse_args()

    vx, vy, yaw = MOVES[args.direction]
    if args.direction == "zero":
        args.seconds = max(args.seconds, 1.0)

    local_ip = detect_local_ip(args.host)
    dog = ZsibotClient(
        dog_ip=args.host,
        local_ip=local_ip,
        local_port=43988,
        variant=args.variant,
    )

    try:
        print(f"raw_move_direction={args.direction}")
        print(f"robot_host={args.host}")
        print(f"local_ip={local_ip}")
        print(f"variant={args.variant}")
        connected = dog.connect(settle_timeout=5.0)
        print(f"connected={connected}")
        if not connected:
            raise RuntimeError("zsibot backend did not connect")

        print(f"battery={dog.battery()}")
        print(f"initial_mode={dog.ctrl_mode()}")
        print("stand_up=true")
        print(f"stand_up_ret={dog.stand_up()}")
        time.sleep(args.stand_wait)
        print(f"mode_after_stand={dog.ctrl_mode()}")

        print("zero_warmup=true")
        print(
            "zero_warmup_ret="
            f"{stream_move(dog, 0.0, 0.0, 0.0, args.warmup_seconds)}"
        )

        print(f"move_vx={vx}")
        print(f"move_vy={vy}")
        print(f"move_yaw={yaw}")
        print(f"move_seconds={args.seconds}")
        print(f"move_ret={stream_move(dog, vx, vy, yaw, args.seconds)}")
        print(f"mode_after_move={dog.ctrl_mode()}")
        print(f"position_after_move={dog.position()}")
        print(f"velocity_after_move={dog.world_velocity()}")

        print("zero_stop=true")
        print(f"zero_stop_ret={stream_move(dog, 0.0, 0.0, 0.0, args.stop_seconds)}")
        print(f"final_mode={dog.ctrl_mode()}")
        print(f"final_velocity={dog.world_velocity()}")
    finally:
        dog.close()


if __name__ == "__main__":
    main()
