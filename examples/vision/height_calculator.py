"""Robot-camera person height estimation.

This is the robot-only vision path. It runs on the dog/robot side and talks
directly to the dog camera while a Raspberry Pi reads the HC-SR04 depth sensor.

Workflow:
  1. Use YOLO/OpenCV to detect the full-body person box in the dog camera frame.
  2. Use Raspberry Pi GPIO to read distance from the HC-SR04 ultrasonic sensor.
  3. Estimate height from pixel height, sensor distance, and camera vertical FOV.
  4. Optional: solve camera vertical FOV once from a known-height reference.

The height estimate uses the pinhole-camera relationship:

    height_cm = distance_cm * pixel_height / focal_length_y_px

The distance should be the distance from the camera/sensor plane to the person.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_RTSP_URL = "rtsp://192.168.234.1:8554/test"
DEFAULT_MODEL = "models/yolov8n.onnx"
DEFAULT_HCSR04_TRIGGER_PIN = 23
DEFAULT_HCSR04_ECHO_PIN = 24


@dataclass(frozen=True)
class LaserDot:
    x: float
    y: float
    radius: float
    area: float


@dataclass(frozen=True)
class PersonBox:
    x: int
    y: int
    width: int
    height: int
    score: float

    @property
    def top_y(self) -> float:
        return float(self.y)

    @property
    def bottom_y(self) -> float:
        return float(self.y + self.height)

    @property
    def center_x(self) -> float:
        return float(self.x + self.width / 2.0)


@dataclass(frozen=True)
class FrameDecision:
    status: str
    reasons: list[str]
    guidance: str


def focal_y_px_from_vertical_fov(*, image_height: int, vertical_fov_deg: float) -> float:
    if vertical_fov_deg <= 0.0 or vertical_fov_deg >= 179.0:
        raise ValueError("--vertical-fov-deg must be between 0 and 179 degrees.")
    return image_height / (2.0 * math.tan(math.radians(vertical_fov_deg) / 2.0))


def vertical_fov_from_focal_y_px(*, image_height: int, focal_y_px: float) -> float:
    if focal_y_px <= 0.0:
        raise ValueError("focal_y_px must be positive.")
    return math.degrees(2.0 * math.atan(image_height / (2.0 * focal_y_px)))


def estimate_height_from_box_fov(
    *,
    person: PersonBox,
    image_height: int,
    distance_cm: float,
    vertical_fov_deg: float,
    camera_pitch_deg: float,
) -> dict[str, float]:
    focal_y_px = focal_y_px_from_vertical_fov(
        image_height=image_height,
        vertical_fov_deg=vertical_fov_deg,
    )
    image_center_y = image_height / 2.0
    top_ray_deg = camera_pitch_deg + math.degrees(math.atan((image_center_y - person.top_y) / focal_y_px))
    bottom_ray_deg = camera_pitch_deg + math.degrees(math.atan((image_center_y - person.bottom_y) / focal_y_px))
    height_cm = distance_cm * (
        math.tan(math.radians(top_ray_deg)) - math.tan(math.radians(bottom_ray_deg))
    )
    angular_height_deg = math.degrees(2.0 * math.atan(person.height / (2.0 * focal_y_px)))
    return {
        "focal_length_y_px": focal_y_px,
        "angular_height_deg": angular_height_deg,
        "camera_pitch_deg": camera_pitch_deg,
        "top_ray_world_deg": top_ray_deg,
        "bottom_ray_world_deg": bottom_ray_deg,
        "person_height_cm": abs(height_cm),
        "person_height_in": abs(height_cm) / 2.54,
    }


def solve_focal_y_px_from_known_height(
    *,
    person: PersonBox,
    image_height: int,
    distance_cm: float,
    known_height_cm: float,
    camera_pitch_deg: float,
) -> float:
    def projected_height(focal_y_px: float) -> float:
        image_center_y = image_height / 2.0
        top_ray_deg = camera_pitch_deg + math.degrees(math.atan((image_center_y - person.top_y) / focal_y_px))
        bottom_ray_deg = camera_pitch_deg + math.degrees(math.atan((image_center_y - person.bottom_y) / focal_y_px))
        return abs(
            distance_cm
            * (math.tan(math.radians(top_ray_deg)) - math.tan(math.radians(bottom_ray_deg)))
        )

    low = image_height * 0.05
    high = image_height * 20.0
    for _ in range(80):
        mid = (low + high) / 2.0
        if projected_height(mid) > known_height_cm:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def require_cv2_numpy():
    try:
        import cv2
        import numpy as np
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "This script needs OpenCV and numpy on the robot/Linux environment. "
            "Install with: python3 -m pip install opencv-python-headless numpy"
        ) from exc
    return cv2, np


def read_hcsr04_distance_cm(
    *,
    trigger_pin: int,
    echo_pin: int,
    samples: int,
    sample_delay_sec: float,
    max_distance_cm: float,
) -> float:
    try:
        from gpiozero import DistanceSensor
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "HC-SR04 distance reading needs gpiozero on the Raspberry Pi. "
            "Install with: python3 -m pip install gpiozero"
        ) from exc

    sensor = DistanceSensor(
        echo=echo_pin,
        trigger=trigger_pin,
        max_distance=max_distance_cm / 100.0,
    )
    distances: list[float] = []
    try:
        time.sleep(0.1)
        for _ in range(max(1, samples)):
            distance_cm = float(sensor.distance) * 100.0
            if math.isfinite(distance_cm) and 0.0 < distance_cm <= max_distance_cm:
                distances.append(distance_cm)
            time.sleep(max(0.0, sample_delay_sec))
    finally:
        sensor.close()

    if not distances:
        raise RuntimeError("HC-SR04 did not return a valid distance sample.")
    distances.sort()
    return distances[len(distances) // 2]


def resolve_distance_cm(args: argparse.Namespace) -> tuple[float, str]:
    if getattr(args, "hcsr04", False):
        distance_cm = read_hcsr04_distance_cm(
            trigger_pin=args.hcsr04_trigger_pin,
            echo_pin=args.hcsr04_echo_pin,
            samples=args.hcsr04_samples,
            sample_delay_sec=args.hcsr04_sample_delay_sec,
            max_distance_cm=args.hcsr04_max_distance_cm,
        )
        return distance_cm, "hcsr04"
    if getattr(args, "distance_cm", None) is not None:
        return float(args.distance_cm), "manual"
    raise RuntimeError("Provide --hcsr04 for live sensor distance or --distance-cm for a manual test value.")


def open_camera(rtsp_url: str):
    cv2, _ = require_cv2_numpy()
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open robot camera stream: {rtsp_url}")
    return cap


def capture_one_frame(*, rtsp_url: str, output: Path, jpeg_quality: int) -> Path:
    cv2, _ = require_cv2_numpy()
    output.parent.mkdir(parents=True, exist_ok=True)
    cap = open_camera(rtsp_url)
    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"Could not read a frame from robot camera: {rtsp_url}")
        cv2.imwrite(str(output), frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
    finally:
        cap.release()
    return output


def detect_laser_dot(
    image,
    *,
    color: str,
    min_area: float,
    max_area: float,
) -> tuple[LaserDot | None, dict[str, object]]:
    cv2, np = require_cv2_numpy()
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    if color == "red":
        mask1 = cv2.inRange(hsv, np.array([0, 90, 120]), np.array([12, 255, 255]))
        mask2 = cv2.inRange(hsv, np.array([168, 90, 120]), np.array([179, 255, 255]))
        mask = cv2.bitwise_or(mask1, mask2)
    elif color == "green":
        mask = cv2.inRange(hsv, np.array([35, 70, 100]), np.array([90, 255, 255]))
    else:
        raise ValueError("laser color must be red or green")

    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, LaserDot]] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < min_area or area > max_area:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue
        x = float(moments["m10"] / moments["m00"])
        y = float(moments["m01"] / moments["m00"])
        (_, _), radius = cv2.minEnclosingCircle(contour)
        x_int = max(0, min(mask.shape[1] - 1, int(round(x))))
        y_int = max(0, min(mask.shape[0] - 1, int(round(y))))
        brightness = float(hsv[y_int, x_int, 2])
        candidates.append((brightness * area, LaserDot(x=x, y=y, radius=float(radius), area=area)))

    if not candidates:
        return None, {"reason": "laser_not_detected", "contours": len(contours)}
    _, dot = max(candidates, key=lambda item: item[0])
    return dot, {"laser_candidates": len(candidates)}


def letterbox(image, size: int):
    cv2, _ = require_cv2_numpy()
    height, width = image.shape[:2]
    scale = min(size / width, size / height)
    new_width = int(round(width * scale))
    new_height = int(round(height * scale))
    resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
    top = (size - new_height) // 2
    bottom = size - new_height - top
    left = (size - new_width) // 2
    right = size - new_width - left
    padded = cv2.copyMakeBorder(
        resized,
        top,
        bottom,
        left,
        right,
        borderType=cv2.BORDER_CONSTANT,
        value=(114, 114, 114),
    )
    return padded, scale, left, top


def detect_people_yolo(
    image_path: Path,
    *,
    model_path: Path,
    confidence_threshold: float,
    nms_threshold: float,
    image_size: int,
) -> list[PersonBox]:
    cv2, np = require_cv2_numpy()
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"OpenCV could not read image: {image_path}")
    if not model_path.exists():
        raise RuntimeError(f"YOLO ONNX model not found: {model_path}")

    original_height, original_width = image.shape[:2]
    padded, scale, pad_x, pad_y = letterbox(image, image_size)
    blob = cv2.dnn.blobFromImage(
        padded,
        scalefactor=1.0 / 255.0,
        size=(image_size, image_size),
        mean=(0, 0, 0),
        swapRB=True,
        crop=False,
    )
    net = cv2.dnn.readNetFromONNX(str(model_path))
    net.setInput(blob)
    output = net.forward()
    predictions = np.squeeze(output)
    if predictions.ndim != 2:
        raise RuntimeError(f"Unexpected YOLO output shape: {output.shape}")
    if predictions.shape[0] == 84:
        predictions = predictions.T

    boxes = []
    scores = []
    for row in predictions:
        # YOLOv8 ONNX exports are normally [cx, cy, w, h, class0, class1, ...].
        # Class 0 is "person" for COCO models such as yolov8n.
        score = float(row[4]) if len(row) == 5 else float(row[4 + 0])
        if score < confidence_threshold:
            continue
        cx, cy, width, height = map(float, row[:4])
        x1 = (cx - width / 2.0 - pad_x) / scale
        y1 = (cy - height / 2.0 - pad_y) / scale
        x2 = (cx + width / 2.0 - pad_x) / scale
        y2 = (cy + height / 2.0 - pad_y) / scale
        x1 = max(0, min(original_width - 1, int(round(x1))))
        y1 = max(0, min(original_height - 1, int(round(y1))))
        x2 = max(0, min(original_width - 1, int(round(x2))))
        y2 = max(0, min(original_height - 1, int(round(y2))))
        if x2 <= x1 or y2 <= y1:
            continue
        boxes.append([x1, y1, x2 - x1, y2 - y1])
        scores.append(score)

    indices = cv2.dnn.NMSBoxes(boxes, scores, confidence_threshold, nms_threshold)
    detections: list[PersonBox] = []
    for raw_index in indices:
        index = int(raw_index[0] if hasattr(raw_index, "__len__") else raw_index)
        x, y, width, height = boxes[index]
        detections.append(PersonBox(x, y, width, height, scores[index]))
    detections.sort(key=lambda box: (box.width * box.height, box.score), reverse=True)
    return detections


def evaluate_person_frame(
    *,
    person: PersonBox | None,
    image_width: int,
    image_height: int,
    edge_margin_px: float,
    min_height_ratio: float,
    max_height_ratio: float,
    center_tolerance_ratio: float,
) -> FrameDecision:
    if person is None:
        return FrameDecision(
            status="reject",
            reasons=["no_person_detected"],
            guidance="aim_at_person",
        )

    reasons: list[str] = []
    if person.top_y <= edge_margin_px:
        reasons.append("head_cut_off")
    if person.bottom_y >= image_height - edge_margin_px:
        reasons.append("feet_cut_off")

    height_ratio = person.height / float(image_height)
    if height_ratio > max_height_ratio:
        reasons.append("person_too_close")
    elif height_ratio < min_height_ratio:
        reasons.append("person_too_far")

    center_error = (person.center_x - (image_width / 2.0)) / float(image_width)
    if center_error < -center_tolerance_ratio:
        reasons.append("person_left_of_center")
    elif center_error > center_tolerance_ratio:
        reasons.append("person_right_of_center")

    if not reasons:
        return FrameDecision(status="ok", reasons=[], guidance="hold_position")
    if "person_too_close" in reasons or "head_cut_off" in reasons or "feet_cut_off" in reasons:
        return FrameDecision(status="reject", reasons=reasons, guidance="move_backward")
    if "person_too_far" in reasons:
        return FrameDecision(status="reject", reasons=reasons, guidance="move_forward")
    if "person_left_of_center" in reasons:
        return FrameDecision(status="reject", reasons=reasons, guidance="move_left")
    if "person_right_of_center" in reasons:
        return FrameDecision(status="reject", reasons=reasons, guidance="move_right")
    return FrameDecision(status="reject", reasons=reasons, guidance="reposition")


def select_person_from_image(args: argparse.Namespace) -> tuple[PersonBox, list[PersonBox], tuple[int, int]]:
    cv2, _ = require_cv2_numpy()
    image = cv2.imread(str(args.image))
    if image is None:
        raise RuntimeError(f"OpenCV could not read image: {args.image}")
    image_height, image_width = image.shape[:2]

    if args.manual_box:
        x, y, width, height = [int(value) for value in args.manual_box.split(",")]
        person = PersonBox(x, y, width, height, 1.0)
        detections = [person]
    else:
        detections = detect_people_yolo(
            Path(args.image),
            model_path=Path(args.yolo_model),
            confidence_threshold=args.yolo_confidence,
            nms_threshold=args.yolo_nms,
            image_size=args.yolo_image_size,
        )
        if len(detections) <= args.person_index:
            raise RuntimeError("No person detected. Try a clearer full-body image.")
        person = detections[args.person_index]

    return person, detections, (image_width, image_height)


def estimate_height_simple(args: argparse.Namespace) -> None:
    person, detections, (image_width, image_height) = select_person_from_image(args)
    distance_cm, distance_source = resolve_distance_cm(args)
    metrics = estimate_height_from_box_fov(
        person=person,
        image_height=image_height,
        distance_cm=distance_cm,
        vertical_fov_deg=args.vertical_fov_deg,
        camera_pitch_deg=args.camera_pitch_deg,
    )
    decision = evaluate_person_frame(
        person=person,
        image_width=image_width,
        image_height=image_height,
        edge_margin_px=args.edge_margin_px,
        min_height_ratio=args.min_person_height_ratio,
        max_height_ratio=args.max_person_height_ratio,
        center_tolerance_ratio=args.center_tolerance_ratio,
    )
    result = {
        "mode": "known_distance_fov",
        "image": args.image,
        "distance_cm": distance_cm,
        "distance_source": distance_source,
        "vertical_fov_deg": args.vertical_fov_deg,
        "camera_pitch_deg": args.camera_pitch_deg,
        "person_index": args.person_index,
        "person_box": asdict(person),
        "frame_status": decision.status,
        "frame_reasons": decision.reasons,
        "guidance": decision.guidance,
        **metrics,
        "detections": [asdict(det) for det in detections],
        "note": (
            "Accuracy depends on distance_cm being the ground distance to the "
            "person plane, camera_pitch_deg matching this frame, and vertical_fov_deg "
            "matching this camera mode/resolution."
        ),
    }
    print(json.dumps(result, indent=2))


def solve_vertical_fov(args: argparse.Namespace) -> None:
    person, detections, (image_width, image_height) = select_person_from_image(args)
    distance_cm, distance_source = resolve_distance_cm(args)
    focal_y_px = solve_focal_y_px_from_known_height(
        person=person,
        image_height=image_height,
        distance_cm=distance_cm,
        known_height_cm=args.known_height_cm,
        camera_pitch_deg=args.camera_pitch_deg,
    )
    vertical_fov_deg = vertical_fov_from_focal_y_px(
        image_height=image_height,
        focal_y_px=focal_y_px,
    )
    result = {
        "mode": "solve_vertical_fov",
        "image": args.image,
        "distance_cm": distance_cm,
        "distance_source": distance_source,
        "known_height_cm": args.known_height_cm,
        "camera_pitch_deg": args.camera_pitch_deg,
        "person_index": args.person_index,
        "person_box": asdict(person),
        "image_width": image_width,
        "image_height": image_height,
        "focal_length_y_px": focal_y_px,
        "vertical_fov_deg": vertical_fov_deg,
        "detections": [asdict(det) for det in detections],
        "next_step": (
            "Use this vertical_fov_deg with measure-height, capture-measure-height, "
            "or guided-measure-height at the same camera resolution."
        ),
    }
    print(json.dumps(result, indent=2))


def capture_and_solve_vertical_fov(args: argparse.Namespace) -> None:
    image_path = capture_one_frame(
        rtsp_url=args.rtsp_url,
        output=Path(args.output),
        jpeg_quality=args.jpeg_quality,
    )
    print(f"captured_image={image_path}")

    solve_args = argparse.Namespace(
        image=str(image_path),
        distance_cm=args.distance_cm,
        hcsr04=args.hcsr04,
        hcsr04_trigger_pin=args.hcsr04_trigger_pin,
        hcsr04_echo_pin=args.hcsr04_echo_pin,
        hcsr04_samples=args.hcsr04_samples,
        hcsr04_sample_delay_sec=args.hcsr04_sample_delay_sec,
        hcsr04_max_distance_cm=args.hcsr04_max_distance_cm,
        known_height_cm=args.known_height_cm,
        camera_pitch_deg=args.camera_pitch_deg,
        yolo_model=args.yolo_model,
        yolo_confidence=args.yolo_confidence,
        yolo_nms=args.yolo_nms,
        yolo_image_size=args.yolo_image_size,
        person_index=args.person_index,
        manual_box=None,
    )
    solve_vertical_fov(solve_args)


def capture_and_estimate_height_simple(args: argparse.Namespace) -> None:
    image_path = capture_one_frame(
        rtsp_url=args.rtsp_url,
        output=Path(args.output),
        jpeg_quality=args.jpeg_quality,
    )
    print(f"captured_image={image_path}")

    estimate_args = argparse.Namespace(
        image=str(image_path),
        distance_cm=args.distance_cm,
        hcsr04=args.hcsr04,
        hcsr04_trigger_pin=args.hcsr04_trigger_pin,
        hcsr04_echo_pin=args.hcsr04_echo_pin,
        hcsr04_samples=args.hcsr04_samples,
        hcsr04_sample_delay_sec=args.hcsr04_sample_delay_sec,
        hcsr04_max_distance_cm=args.hcsr04_max_distance_cm,
        vertical_fov_deg=args.vertical_fov_deg,
        camera_pitch_deg=args.camera_pitch_deg,
        yolo_model=args.yolo_model,
        yolo_confidence=args.yolo_confidence,
        yolo_nms=args.yolo_nms,
        yolo_image_size=args.yolo_image_size,
        person_index=args.person_index,
        manual_box=None,
        edge_margin_px=args.edge_margin_px,
        min_person_height_ratio=args.min_person_height_ratio,
        max_person_height_ratio=args.max_person_height_ratio,
        center_tolerance_ratio=args.center_tolerance_ratio,
    )
    estimate_height_simple(estimate_args)


def prompt_for_guidance(guidance: str) -> str:
    prompts = {
        "hold_position": "Hold still.",
        "aim_at_person": "Please stand where I can see your full body.",
        "move_backward": "Please step back to the line.",
        "move_forward": "Please step forward to the line.",
        "move_left": "Please step a little to your left.",
        "move_right": "Please step a little to your right.",
        "reposition": "Please reposition so I can see your full body.",
    }
    return prompts.get(guidance, "Please reposition.")


def guided_measure_height(args: argparse.Namespace) -> None:
    cv2, _ = require_cv2_numpy()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("mode=guided_measure_height")
    if args.hcsr04:
        print("distance_source=hcsr04")
    else:
        print(f"target_distance_cm={args.distance_cm:.1f}")
    print(f"vertical_fov_deg={args.vertical_fov_deg:.3f}")
    print(f"camera_pitch_deg={args.camera_pitch_deg:.3f}")
    print("reference=hcsr04_distance_sensor")

    for attempt in range(1, args.max_attempts + 1):
        image_path = output_dir / f"guided_{attempt:02d}.jpg"
        capture_one_frame(rtsp_url=args.rtsp_url, output=image_path, jpeg_quality=args.jpeg_quality)
        image = cv2.imread(str(image_path))
        if image is None:
            raise RuntimeError(f"OpenCV could not read captured image: {image_path}")
        image_height, image_width = image.shape[:2]

        detections = detect_people_yolo(
            image_path,
            model_path=Path(args.yolo_model),
            confidence_threshold=args.yolo_confidence,
            nms_threshold=args.yolo_nms,
            image_size=args.yolo_image_size,
        )
        person = detections[args.person_index] if len(detections) > args.person_index else None
        decision = evaluate_person_frame(
            person=person,
            image_width=image_width,
            image_height=image_height,
            edge_margin_px=args.edge_margin_px,
            min_height_ratio=args.min_person_height_ratio,
            max_height_ratio=args.max_person_height_ratio,
            center_tolerance_ratio=args.center_tolerance_ratio,
        )

        print(f"attempt={attempt}")
        print(f"image={image_path}")
        print(f"detections={len(detections)}")
        print(f"frame_status={decision.status}")
        print(f"frame_reasons={','.join(decision.reasons) if decision.reasons else 'none'}")
        print(f"guidance={decision.guidance}")
        print(f"say={prompt_for_guidance(decision.guidance)}")

        if decision.status == "ok" and person is not None:
            distance_cm, distance_source = resolve_distance_cm(args)
            metrics = estimate_height_from_box_fov(
                person=person,
                image_height=image_height,
                distance_cm=distance_cm,
                vertical_fov_deg=args.vertical_fov_deg,
                camera_pitch_deg=args.camera_pitch_deg,
            )
            result = {
                "mode": "guided_measure_height",
                "image": str(image_path),
                "distance_cm": distance_cm,
                "distance_source": distance_source,
                "vertical_fov_deg": args.vertical_fov_deg,
                "camera_pitch_deg": args.camera_pitch_deg,
                "person_index": args.person_index,
                "person_box": asdict(person),
                **metrics,
                "detections": [asdict(det) for det in detections],
            }
            print(json.dumps(result, indent=2))
            return

        if attempt < args.max_attempts:
            time.sleep(args.settle_seconds)

    print("guided_measure_height=failed")
    print("reason=could_not_get_reliable_full_body_frame")


def verify_yolo(args: argparse.Namespace) -> None:
    cv2, _ = require_cv2_numpy()
    model_path = Path(args.yolo_model)
    if not model_path.exists():
        raise RuntimeError(f"YOLO ONNX model not found: {model_path}")
    cv2.dnn.readNetFromONNX(str(model_path))
    print(f"yolo_model={model_path}")
    print("yolo_runtime=opencv_dnn")
    print("yolo_loaded=true")


def inspect_laser(args: argparse.Namespace) -> None:
    cv2, _ = require_cv2_numpy()
    image = cv2.imread(str(args.image))
    if image is None:
        raise RuntimeError(f"OpenCV could not read image: {args.image}")
    dot, debug = detect_laser_dot(
        image,
        color=args.laser_color,
        min_area=args.laser_min_area,
        max_area=args.laser_max_area,
    )
    result = {
        "image": args.image,
        "laser_color": args.laser_color,
        "laser_detected": dot is not None,
        "laser_dot": None if dot is None else asdict(dot),
        "debug": debug,
    }
    print(json.dumps(result, indent=2))


def read_distance(args: argparse.Namespace) -> None:
    distance_cm, distance_source = resolve_distance_cm(args)
    print(
        json.dumps(
            {
                "distance_source": distance_source,
                "distance_cm": distance_cm,
                "distance_in": distance_cm / 2.54,
            },
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_yolo_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--yolo-model", default=DEFAULT_MODEL)
        p.add_argument("--yolo-confidence", type=float, default=0.35)
        p.add_argument("--yolo-nms", type=float, default=0.45)
        p.add_argument("--yolo-image-size", type=int, default=640)
        p.add_argument("--person-index", type=int, default=0)

    def add_frame_quality_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--edge-margin-px", type=float, default=20.0)
        p.add_argument("--min-person-height-ratio", type=float, default=0.35)
        p.add_argument("--max-person-height-ratio", type=float, default=0.88)
        p.add_argument("--center-tolerance-ratio", type=float, default=0.18)

    def add_camera_pose_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--camera-pitch-deg",
            type=float,
            default=0.0,
            help="Camera tilt for this frame; positive means camera points upward.",
        )

    def add_laser_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--laser-color", choices=["red", "green"], default="green")
        p.add_argument("--laser-min-area", type=float, default=3.0)
        p.add_argument("--laser-max-area", type=float, default=1800.0)

    def add_hcsr04_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--hcsr04", action="store_true", help="Read distance from Raspberry Pi HC-SR04 GPIO.")
        p.add_argument("--hcsr04-trigger-pin", type=int, default=DEFAULT_HCSR04_TRIGGER_PIN)
        p.add_argument("--hcsr04-echo-pin", type=int, default=DEFAULT_HCSR04_ECHO_PIN)
        p.add_argument("--hcsr04-samples", type=int, default=5)
        p.add_argument("--hcsr04-sample-delay-sec", type=float, default=0.06)
        p.add_argument("--hcsr04-max-distance-cm", type=float, default=400.0)

    inspect_laser_cmd = sub.add_parser("inspect-laser", help="Check whether a red/green laser dot is visible.")
    inspect_laser_cmd.add_argument("--image", required=True)
    add_laser_args(inspect_laser_cmd)
    inspect_laser_cmd.set_defaults(func=inspect_laser)

    verify = sub.add_parser("verify-yolo", help="Verify that the YOLO ONNX model loads.")
    verify.add_argument("--yolo-model", default=DEFAULT_MODEL)
    verify.set_defaults(func=verify_yolo)

    distance = sub.add_parser("read-distance", help="Read distance from Raspberry Pi HC-SR04 GPIO.")
    add_hcsr04_args(distance)
    distance.set_defaults(hcsr04=True, distance_cm=None, func=read_distance)

    solve_fov = sub.add_parser(
        "solve-fov",
        help="Solve camera vertical FOV from one known-height person/object and HC-SR04 distance.",
    )
    solve_fov.add_argument("--image", required=True)
    solve_fov.add_argument("--distance-cm", type=float, default=None, help="Manual test distance in cm.")
    solve_fov.add_argument("--known-height-cm", type=float, required=True)
    solve_fov.add_argument(
        "--manual-box",
        default=None,
        help="Fallback reference box as x,y,width,height when YOLO is not ready.",
    )
    add_hcsr04_args(solve_fov)
    add_camera_pose_args(solve_fov)
    add_yolo_args(solve_fov)
    solve_fov.set_defaults(func=solve_vertical_fov)

    capture_solve_fov = sub.add_parser(
        "capture-solve-fov",
        help="Capture a live reference frame and solve camera vertical FOV with HC-SR04 distance.",
    )
    capture_solve_fov.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    capture_solve_fov.add_argument("--output", default="known_person_capture.jpg")
    capture_solve_fov.add_argument("--jpeg-quality", type=int, default=92)
    capture_solve_fov.add_argument("--distance-cm", type=float, default=None, help="Manual test distance in cm.")
    capture_solve_fov.add_argument("--known-height-cm", type=float, required=True)
    add_hcsr04_args(capture_solve_fov)
    add_camera_pose_args(capture_solve_fov)
    add_yolo_args(capture_solve_fov)
    capture_solve_fov.set_defaults(func=capture_and_solve_vertical_fov)

    simple_estimate = sub.add_parser(
        "measure-height",
        help="Estimate height with YOLO, HC-SR04 distance, and camera vertical FOV.",
    )
    simple_estimate.add_argument("--image", required=True)
    simple_estimate.add_argument("--distance-cm", type=float, default=None, help="Manual test distance in cm.")
    simple_estimate.add_argument("--vertical-fov-deg", type=float, required=True)
    simple_estimate.add_argument(
        "--manual-box",
        default=None,
        help="Fallback person box as x,y,width,height when YOLO is not ready.",
    )
    add_hcsr04_args(simple_estimate)
    add_camera_pose_args(simple_estimate)
    add_yolo_args(simple_estimate)
    add_frame_quality_args(simple_estimate)
    simple_estimate.set_defaults(func=estimate_height_simple)

    simple_live = sub.add_parser(
        "capture-measure-height",
        help="Capture one robot-camera frame and estimate height from HC-SR04 distance and vertical FOV.",
    )
    simple_live.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    simple_live.add_argument("--output", default="person_capture.jpg")
    simple_live.add_argument("--jpeg-quality", type=int, default=92)
    simple_live.add_argument("--distance-cm", type=float, default=None, help="Manual test distance in cm.")
    simple_live.add_argument("--vertical-fov-deg", type=float, required=True)
    add_hcsr04_args(simple_live)
    add_camera_pose_args(simple_live)
    add_yolo_args(simple_live)
    add_frame_quality_args(simple_live)
    simple_live.set_defaults(func=capture_and_estimate_height_simple)

    guided = sub.add_parser(
        "guided-measure-height",
        help="Guide a person into frame, then estimate height from HC-SR04 distance.",
    )
    guided.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    guided.add_argument("--output-dir", default="person_measure_runs/latest")
    guided.add_argument("--jpeg-quality", type=int, default=92)
    guided.add_argument("--distance-cm", type=float, default=None, help="Manual test distance in cm.")
    guided.add_argument("--vertical-fov-deg", type=float, required=True)
    guided.add_argument("--max-attempts", type=int, default=8)
    guided.add_argument("--settle-seconds", type=float, default=1.5)
    add_hcsr04_args(guided)
    add_camera_pose_args(guided)
    add_yolo_args(guided)
    add_frame_quality_args(guided)
    guided.set_defaults(func=guided_measure_height)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
