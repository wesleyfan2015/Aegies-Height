"""Robot-camera calibration and person height estimation.

This is the robot-only vision path. It runs on the dog/robot side and talks
directly to the dog camera.

Workflow:
  1. Capture many robot-camera frames of the wall grid.
  2. Calibrate the camera from the detected grid intersections.
  3. Detect a person with YOLO and combine the pixel height with radar distance.

Robot repositioning uses the raw zsibot backend, not sess.motion.cmd_vel.

The height estimate uses calibrated camera intrinsics:

    height_cm = distance_cm * abs(y_bottom_normalized - y_top_normalized)

That is the pinhole-camera relationship after undistorting image points. Radar
should provide the distance from the robot camera plane to the person.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


DEFAULT_RTSP_URL = "rtsp://192.168.234.1:8554/test"
DEFAULT_MODEL = "models/yolov8n.onnx"


@dataclass(frozen=True)
class GridSpec:
    rows: int
    cols: int
    square_size_cm: float

    @property
    def box_rows(self) -> int:
        return self.rows - 1

    @property
    def box_cols(self) -> int:
        return self.cols - 1


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


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def capture_frames(args: argparse.Namespace) -> None:
    cv2, _ = require_cv2_numpy()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cap = open_camera(args.rtsp_url)
    saved = 0
    accepted = 0
    records: list[dict[str, object]] = []
    next_capture_at = time.monotonic()
    started_at = datetime.now().isoformat(timespec="seconds")

    print(f"capture_started_at={started_at}")
    print(f"rtsp_url={args.rtsp_url}")
    print(f"target_frames={args.count}")
    print(f"output_dir={output_dir}")

    try:
        while saved < args.count:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("camera_read=failed_reconnecting")
                cap.release()
                time.sleep(0.5)
                cap = open_camera(args.rtsp_url)
                continue

            now = time.monotonic()
            if now < next_capture_at:
                continue
            next_capture_at = now + args.interval_sec

            saved += 1
            image_path = output_dir / f"grid_{saved:06d}.jpg"
            cv2.imwrite(str(image_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), args.jpeg_quality])

            found, points, debug = detect_grid_points(
                frame,
                GridSpec(args.grid_rows, args.grid_cols, args.square_size_cm),
                blue_hue_low=args.blue_hue_low,
                blue_hue_high=args.blue_hue_high,
                min_line_length=args.min_line_length,
            )
            if found:
                accepted += 1
            records.append(
                {
                    "image": str(image_path),
                    "grid_found": found,
                    "point_count": 0 if points is None else int(len(points)),
                    "debug": debug,
                }
            )
            if saved % args.progress_every == 0 or found:
                print(
                    f"saved={saved} accepted={accepted} "
                    f"last_grid_found={str(found).lower()} image={image_path}"
                )
    finally:
        cap.release()

    save_json(
        output_dir / "capture_records.json",
        {
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "rtsp_url": args.rtsp_url,
            "saved_count": saved,
            "accepted_count": accepted,
            "grid": asdict(GridSpec(args.grid_rows, args.grid_cols, args.square_size_cm)),
            "records": records,
        },
    )
    print(f"capture_complete=true saved={saved} accepted={accepted}")


def group_close_values(values: list[float], tolerance: float) -> list[float]:
    if not values:
        return []
    grouped: list[list[float]] = []
    for value in sorted(values):
        if not grouped or abs(value - sum(grouped[-1]) / len(grouped[-1])) > tolerance:
            grouped.append([value])
        else:
            grouped[-1].append(value)
    return [sum(group) / len(group) for group in grouped]


def detect_grid_points(
    image,
    spec: GridSpec,
    *,
    blue_hue_low: int,
    blue_hue_high: int,
    min_line_length: int,
):
    """Detect blue tape grid intersections from an image.

    The wall target in test_camera.jpg is a blue-tape grid. This detector finds
    blue pixels, extracts long horizontal/vertical line segments, groups them,
    and returns their intersections as calibration points.
    """

    cv2, np = require_cv2_numpy()
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lower = np.array([blue_hue_low, 50, 35], dtype=np.uint8)
    upper = np.array([blue_hue_high, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    lines = cv2.HoughLinesP(
        mask,
        rho=1,
        theta=np.pi / 180.0,
        threshold=80,
        minLineLength=min_line_length,
        maxLineGap=25,
    )
    if lines is None:
        return False, None, {"reason": "no_blue_grid_lines"}

    vertical_x: list[float] = []
    horizontal_y: list[float] = []
    for raw in lines.reshape(-1, 4):
        x1, y1, x2, y2 = [float(v) for v in raw]
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length < min_line_length:
            continue
        if abs(dx) < max(12.0, abs(dy) * 0.25):
            vertical_x.append((x1 + x2) / 2.0)
        elif abs(dy) < max(12.0, abs(dx) * 0.25):
            horizontal_y.append((y1 + y2) / 2.0)

    x_lines = group_close_values(vertical_x, tolerance=22.0)
    y_lines = group_close_values(horizontal_y, tolerance=22.0)

    if len(x_lines) < spec.cols or len(y_lines) < spec.rows:
        return (
            False,
            None,
            {
                "reason": "not_enough_grid_lines",
                "vertical_lines": len(x_lines),
                "horizontal_lines": len(y_lines),
            },
        )

    x_lines = sorted(x_lines)[: spec.cols]
    y_lines = sorted(y_lines)[: spec.rows]
    points = np.array([[x, y] for y in y_lines for x in x_lines], dtype=np.float32)
    return True, points.reshape(-1, 1, 2), {
        "vertical_lines": len(x_lines),
        "horizontal_lines": len(y_lines),
    }


def object_points(spec: GridSpec):
    _, np = require_cv2_numpy()
    points = []
    for row in range(spec.rows):
        for col in range(spec.cols):
            points.append([col * spec.square_size_cm, row * spec.square_size_cm, 0.0])
    return np.asarray(points, dtype=np.float32)


def grid_box_center_object_point(*, spec: GridSpec, row: int, col: int):
    _, np = require_cv2_numpy()
    if row < 1 or row > spec.box_rows:
        raise ValueError(f"box row must be 1..{spec.box_rows}, got {row}")
    if col < 1 or col > spec.box_cols:
        raise ValueError(f"box col must be 1..{spec.box_cols}, got {col}")
    x_cm = (col - 0.5) * spec.square_size_cm
    y_cm = (row - 0.5) * spec.square_size_cm
    return np.asarray([[x_cm, y_cm, 0.0]], dtype=np.float32)


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


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, separators=(",", ":")) + "\n")


def load_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def capture_laser_samples(args: argparse.Namespace) -> None:
    cv2, _ = require_cv2_numpy()
    spec = GridSpec(args.grid_rows, args.grid_cols, args.square_size_cm)
    output_dir = Path(args.output_dir)
    samples_path = Path(args.samples)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"laser_samples={samples_path}")
    print(f"grid_boxes={spec.box_rows}x{spec.box_cols}")
    print("box_labels_are=1_based_from_top_left")

    for index in range(1, args.count + 1):
        if args.interactive:
            raw = input(f"sample {index}/{args.count} box row,col (or q)> ").strip().lower()
            if raw in {"q", "quit", "exit"}:
                break
            row, col = [int(part.strip()) for part in raw.split(",", 1)]
        else:
            if args.box_row is None or args.box_col is None:
                raise RuntimeError("Use --interactive or provide --box-row and --box-col.")
            row, col = args.box_row, args.box_col

        grid_box_center_object_point(spec=spec, row=row, col=col)
        image_path = output_dir / f"laser_{int(time.time())}_{index:04d}.jpg"
        capture_one_frame(rtsp_url=args.rtsp_url, output=image_path, jpeg_quality=args.jpeg_quality)
        image = cv2.imread(str(image_path))
        if image is None:
            raise RuntimeError(f"OpenCV could not read captured image: {image_path}")

        dot, laser_debug = detect_laser_dot(
            image,
            color=args.laser_color,
            min_area=args.laser_min_area,
            max_area=args.laser_max_area,
        )
        grid_found, grid_points, grid_debug = detect_grid_points(
            image,
            spec,
            blue_hue_low=args.blue_hue_low,
            blue_hue_high=args.blue_hue_high,
            min_line_length=args.min_line_length,
        )
        sample = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "image": str(image_path),
            "box_row": row,
            "box_col": col,
            "box_origin": "top_left",
            "grid": asdict(spec),
            "laser_color": args.laser_color,
            "laser_detected": dot is not None,
            "laser_dot": None if dot is None else asdict(dot),
            "grid_found": grid_found,
            "grid_point_count": 0 if grid_points is None else int(len(grid_points)),
            "laser_debug": laser_debug,
            "grid_debug": grid_debug,
            "label": args.label,
        }
        append_jsonl(samples_path, sample)
        print(
            f"sample_saved={image_path} box={row},{col} "
            f"laser_detected={str(dot is not None).lower()} grid_found={str(grid_found).lower()}"
        )


def calibrate_from_images(args: argparse.Namespace) -> None:
    cv2, np = require_cv2_numpy()
    spec = GridSpec(args.grid_rows, args.grid_cols, args.square_size_cm)
    image_dir = Path(args.image_dir)
    images = sorted(image_dir.glob("*.jpg")) + sorted(image_dir.glob("*.png"))
    if not images:
        raise RuntimeError(f"No calibration images found in {image_dir}")

    object_sets = []
    image_sets = []
    accepted: list[str] = []
    rejected: list[dict[str, object]] = []
    image_size: tuple[int, int] | None = None
    object_template = object_points(spec)

    for image_path in images:
        image = cv2.imread(str(image_path))
        if image is None:
            rejected.append({"image": str(image_path), "reason": "opencv_read_failed"})
            continue
        height, width = image.shape[:2]
        image_size = (width, height)
        found, points, debug = detect_grid_points(
            image,
            spec,
            blue_hue_low=args.blue_hue_low,
            blue_hue_high=args.blue_hue_high,
            min_line_length=args.min_line_length,
        )
        if not found or points is None:
            rejected.append({"image": str(image_path), **debug})
            continue
        object_sets.append(object_template)
        image_sets.append(points)
        accepted.append(str(image_path))

    if image_size is None:
        raise RuntimeError("OpenCV could not read any calibration image.")
    if len(accepted) < args.min_accepted:
        raise RuntimeError(
            f"Need at least {args.min_accepted} accepted grid images, got {len(accepted)}. "
            "Capture more images with the grid visible from different angles."
        )

    rms, camera_matrix, distortion, rvecs, tvecs = cv2.calibrateCamera(
        object_sets,
        image_sets,
        image_size,
        None,
        None,
    )
    if not np.isfinite(camera_matrix).all() or not np.isfinite(distortion).all():
        raise RuntimeError("Calibration produced non-finite values; improve grid coverage.")

    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "image_dir": str(image_dir),
        "image_width": image_size[0],
        "image_height": image_size[1],
        "grid": asdict(spec),
        "attempt_count": len(images),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "rms_reprojection_error": float(rms),
        "camera_matrix": camera_matrix.tolist(),
        "distortion_coefficients": distortion.ravel().tolist(),
        "accepted_images": accepted,
        "rejected_images": rejected,
        "note": (
            "This calibration uses the blue tape grid. For best accuracy, keep "
            "the grid flat, measure square_size_cm carefully, and capture many "
            "angles/distances across the full image."
        ),
    }
    save_json(Path(args.output), report)
    print(f"calibration_saved={args.output}")
    print(f"accepted_count={len(accepted)}")
    print(f"rms_reprojection_error={float(rms):.6f}")


def calibrate_from_laser_samples(args: argparse.Namespace) -> None:
    cv2, np = require_cv2_numpy()
    spec = GridSpec(args.grid_rows, args.grid_cols, args.square_size_cm)
    samples = load_jsonl(Path(args.samples))
    if not samples:
        raise RuntimeError(f"No laser samples found: {args.samples}")

    object_sets = []
    image_sets = []
    accepted: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    image_size: tuple[int, int] | None = None
    base_object_points = object_points(spec).reshape(-1, 3)

    for sample in samples:
        image_path = Path(str(sample["image"]))
        image = cv2.imread(str(image_path))
        if image is None:
            rejected.append({"image": str(image_path), "reason": "opencv_read_failed"})
            continue
        height, width = image.shape[:2]
        image_size = (width, height)

        found, grid_points, grid_debug = detect_grid_points(
            image,
            spec,
            blue_hue_low=args.blue_hue_low,
            blue_hue_high=args.blue_hue_high,
            min_line_length=args.min_line_length,
        )
        if not found or grid_points is None:
            rejected.append({"image": str(image_path), **grid_debug})
            continue

        laser_dot = sample.get("laser_dot")
        if not isinstance(laser_dot, dict):
            rejected.append({"image": str(image_path), "reason": "laser_dot_missing"})
            continue

        row = int(sample["box_row"])
        col = int(sample["box_col"])
        laser_object = grid_box_center_object_point(spec=spec, row=row, col=col)
        laser_image = np.asarray([[[float(laser_dot["x"]), float(laser_dot["y"])]]], dtype=np.float32)

        object_set = np.vstack([base_object_points, laser_object.reshape(-1, 3)])
        image_set = np.vstack([grid_points.reshape(-1, 2), laser_image.reshape(-1, 2)]).reshape(-1, 1, 2)
        object_sets.append(object_set.astype(np.float32))
        image_sets.append(image_set.astype(np.float32))
        accepted.append({"image": str(image_path), "box_row": row, "box_col": col})

    if image_size is None:
        raise RuntimeError("OpenCV could not read any laser sample image.")
    if len(accepted) < args.min_accepted:
        raise RuntimeError(
            f"Need at least {args.min_accepted} accepted laser samples, got {len(accepted)}."
        )

    flags = 0
    if args.fix_principal_point:
        flags |= cv2.CALIB_FIX_PRINCIPAL_POINT
    rms, camera_matrix, distortion, rvecs, tvecs = cv2.calibrateCamera(
        object_sets,
        image_sets,
        image_size,
        None,
        None,
        flags=flags,
    )
    if not np.isfinite(camera_matrix).all() or not np.isfinite(distortion).all():
        raise RuntimeError("Calibration produced non-finite values; improve laser/grid samples.")

    laser_errors: list[float] = []
    grid_count = spec.rows * spec.cols
    for object_set, image_set, rvec, tvec in zip(object_sets, image_sets, rvecs, tvecs):
        projected, _ = cv2.projectPoints(object_set, rvec, tvec, camera_matrix, distortion)
        actual = image_set.reshape(-1, 2)
        projected_2d = projected.reshape(-1, 2)
        laser_errors.append(float(np.linalg.norm(projected_2d[grid_count] - actual[grid_count])))

    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": "laser_grid_samples",
        "samples": args.samples,
        "image_width": image_size[0],
        "image_height": image_size[1],
        "grid": asdict(spec),
        "attempt_count": len(samples),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "rms_reprojection_error": float(rms),
        "laser_reprojection_error_px_avg": float(sum(laser_errors) / len(laser_errors)),
        "laser_reprojection_error_px_max": float(max(laser_errors)),
        "camera_matrix": camera_matrix.tolist(),
        "distortion_coefficients": distortion.ravel().tolist(),
        "accepted_samples": accepted,
        "rejected_samples": rejected,
    }
    save_json(Path(args.output), report)
    print(f"calibration_saved={args.output}")
    print(f"accepted_count={len(accepted)}")
    print(f"rms_reprojection_error={float(rms):.6f}")
    print(f"laser_error_px_avg={report['laser_reprojection_error_px_avg']:.3f}")


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


def raw_robot_move(
    *,
    robot_host: str,
    variant: str,
    vx: float,
    vy: float,
    yaw_rate: float,
    seconds: float,
    local_port: int,
) -> None:
    try:
        from ff_sdk.internal.oem.zsibot import ZsibotClient, detect_local_ip
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Raw robot movement needs ff_sdk on the robot/Linux environment."
        ) from exc

    local_ip = detect_local_ip(robot_host)
    dog = ZsibotClient(
        dog_ip=robot_host,
        local_ip=local_ip,
        local_port=local_port,
        variant=variant,
    )
    try:
        connected = dog.connect(settle_timeout=5.0)
        if not connected:
            raise RuntimeError("raw zsibot backend did not connect")
        dog.stand_up()
        time.sleep(2.0)
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            dog.move(vx, vy, yaw_rate)
            time.sleep(0.05)
        stop_end = time.monotonic() + 0.6
        while time.monotonic() < stop_end:
            dog.move(0.0, 0.0, 0.0)
            time.sleep(0.05)
    finally:
        dog.close()


def move_for_guidance(args: argparse.Namespace, guidance: str) -> None:
    motions = {
        "move_backward": (-abs(args.nudge_speed_mps), 0.0, 0.0),
        "move_forward": (abs(args.nudge_speed_mps), 0.0, 0.0),
        "move_left": (0.0, abs(args.lateral_speed_mps), 0.0),
        "move_right": (0.0, -abs(args.lateral_speed_mps), 0.0),
    }
    if guidance not in motions:
        print(f"motion_skipped_guidance={guidance}")
        return
    vx, vy, yaw_rate = motions[guidance]
    print(f"motion_guidance={guidance}")
    print(f"motion_execute={str(args.execute_motion).lower()}")
    print(f"motion_vx={vx:.3f}")
    print(f"motion_vy={vy:.3f}")
    print(f"motion_seconds={args.nudge_seconds:.2f}")
    if not args.execute_motion:
        print("planned_motion_only=true")
        return
    raw_robot_move(
        robot_host=args.robot_host,
        variant=args.variant,
        vx=vx,
        vy=vy,
        yaw_rate=yaw_rate,
        seconds=args.nudge_seconds,
        local_port=args.local_port,
    )


def normalized_y_delta(
    *,
    calibration: dict[str, object],
    center_x: float,
    top_y: float,
    bottom_y: float,
) -> float:
    cv2, np = require_cv2_numpy()
    camera_matrix = np.asarray(calibration["camera_matrix"], dtype=np.float64)
    distortion = np.asarray(calibration["distortion_coefficients"], dtype=np.float64)
    pts = np.asarray([[[center_x, top_y]], [[center_x, bottom_y]]], dtype=np.float64)
    undistorted = cv2.undistortPoints(pts, camera_matrix, distortion)
    top_norm = float(undistorted[0, 0, 1])
    bottom_norm = float(undistorted[1, 0, 1])
    return abs(bottom_norm - top_norm)


def estimate_height(args: argparse.Namespace) -> None:
    calibration = load_json(Path(args.calibration))
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
        if not detections:
            raise RuntimeError("No person detected. Try a clearer full-body image.")
        person = detections[args.person_index]

    y_delta = normalized_y_delta(
        calibration=calibration,
        center_x=person.center_x,
        top_y=person.top_y,
        bottom_y=person.bottom_y,
    )
    height_cm = args.distance_cm * y_delta
    result = {
        "image": args.image,
        "distance_cm": args.distance_cm,
        "person_index": args.person_index,
        "person_box": asdict(person),
        "normalized_y_delta": y_delta,
        "person_height_cm": height_cm,
        "person_height_in": height_cm / 2.54,
        "detections": [asdict(det) for det in detections],
    }
    print(json.dumps(result, indent=2))


def capture_and_estimate_height(args: argparse.Namespace) -> None:
    image_path = capture_one_frame(
        rtsp_url=args.rtsp_url,
        output=Path(args.output),
        jpeg_quality=args.jpeg_quality,
    )
    print(f"captured_image={image_path}")

    estimate_args = argparse.Namespace(
        image=str(image_path),
        distance_cm=args.distance_cm,
        calibration=args.calibration,
        yolo_model=args.yolo_model,
        yolo_confidence=args.yolo_confidence,
        yolo_nms=args.yolo_nms,
        yolo_image_size=args.yolo_image_size,
        person_index=args.person_index,
        manual_box=None,
    )
    estimate_height(estimate_args)


def auto_capture_height(args: argparse.Namespace) -> None:
    cv2, _ = require_cv2_numpy()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("mode=auto_capture_height")
    print(f"execute_motion={str(args.execute_motion).lower()}")
    print(f"max_attempts={args.max_attempts}")

    for attempt in range(1, args.max_attempts + 1):
        image_path = output_dir / f"attempt_{attempt:02d}.jpg"
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
        if person is not None:
            print(f"person_box={asdict(person)}")
            print(f"person_height_ratio={person.height / float(image_height):.3f}")
        print(f"frame_status={decision.status}")
        print(f"frame_reasons={','.join(decision.reasons) if decision.reasons else 'none'}")
        print(f"guidance={decision.guidance}")

        if decision.status == "ok":
            estimate_args = argparse.Namespace(
                image=str(image_path),
                distance_cm=args.distance_cm,
                calibration=args.calibration,
                yolo_model=args.yolo_model,
                yolo_confidence=args.yolo_confidence,
                yolo_nms=args.yolo_nms,
                yolo_image_size=args.yolo_image_size,
                person_index=args.person_index,
                manual_box=None,
            )
            estimate_height(estimate_args)
            return

        if attempt >= args.max_attempts:
            break
        move_for_guidance(args, decision.guidance)
        time.sleep(args.settle_seconds)

    print("auto_capture_height=failed")
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


def inspect_grid(args: argparse.Namespace) -> None:
    cv2, _ = require_cv2_numpy()
    image = cv2.imread(str(args.image))
    if image is None:
        raise RuntimeError(f"OpenCV could not read image: {args.image}")
    found, points, debug = detect_grid_points(
        image,
        GridSpec(args.grid_rows, args.grid_cols, args.square_size_cm),
        blue_hue_low=args.blue_hue_low,
        blue_hue_high=args.blue_hue_high,
        min_line_length=args.min_line_length,
    )
    print(f"grid_found={str(found).lower()}")
    print(f"point_count={0 if points is None else len(points)}")
    print(json.dumps(debug, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_grid_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--grid-rows", type=int, default=8, help="Detected horizontal grid intersections.")
        p.add_argument("--grid-cols", type=int, default=8, help="Detected vertical grid intersections.")
        p.add_argument("--square-size-cm", type=float, default=10.0, help="Measured grid square size.")
        p.add_argument("--blue-hue-low", type=int, default=85)
        p.add_argument("--blue-hue-high", type=int, default=135)
        p.add_argument("--min-line-length", type=int, default=80)

    inspect = sub.add_parser("inspect-grid", help="Check whether the grid is detectable in one image.")
    inspect.add_argument("--image", default="test_camera.jpg")
    add_grid_args(inspect)
    inspect.set_defaults(func=inspect_grid)

    capture = sub.add_parser("capture-grid", help="Capture many robot-camera grid frames.")
    capture.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    capture.add_argument("--output-dir", default="camera_calibration_runs/latest/images")
    capture.add_argument("--count", type=int, default=1000)
    capture.add_argument("--interval-sec", type=float, default=0.05)
    capture.add_argument("--jpeg-quality", type=int, default=92)
    capture.add_argument("--progress-every", type=int, default=50)
    add_grid_args(capture)
    capture.set_defaults(func=capture_frames)

    laser = sub.add_parser(
        "capture-laser-samples",
        help="Capture labeled samples where a laser points into a known grid box.",
    )
    laser.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    laser.add_argument("--output-dir", default="camera_calibration_runs/latest/laser_images")
    laser.add_argument("--samples", default="camera_calibration_runs/latest/laser_samples.jsonl")
    laser.add_argument("--count", type=int, default=20)
    laser.add_argument("--interactive", action="store_true")
    laser.add_argument("--box-row", type=int, default=None, help="1-based grid box row from top.")
    laser.add_argument("--box-col", type=int, default=None, help="1-based grid box column from left.")
    laser.add_argument("--label", default="")
    laser.add_argument("--jpeg-quality", type=int, default=92)
    laser.add_argument("--laser-color", choices=["red", "green"], default="red")
    laser.add_argument("--laser-min-area", type=float, default=3.0)
    laser.add_argument("--laser-max-area", type=float, default=1800.0)
    add_grid_args(laser)
    laser.set_defaults(func=capture_laser_samples)

    calibrate = sub.add_parser("calibrate", help="Calibrate camera from captured grid images.")
    calibrate.add_argument("--image-dir", default="camera_calibration_runs/latest/images")
    calibrate.add_argument("--output", default="camera_calibration_runs/latest/calibration.json")
    calibrate.add_argument("--min-accepted", type=int, default=30)
    add_grid_args(calibrate)
    calibrate.set_defaults(func=calibrate_from_images)

    laser_calibrate = sub.add_parser(
        "calibrate-laser",
        help="Calibrate from images where laser dots are labeled by grid box.",
    )
    laser_calibrate.add_argument("--samples", default="camera_calibration_runs/latest/laser_samples.jsonl")
    laser_calibrate.add_argument("--output", default="camera_calibration_runs/latest/calibration.json")
    laser_calibrate.add_argument("--min-accepted", type=int, default=10)
    laser_calibrate.add_argument("--fix-principal-point", action="store_true")
    add_grid_args(laser_calibrate)
    laser_calibrate.set_defaults(func=calibrate_from_laser_samples)

    verify = sub.add_parser("verify-yolo", help="Verify that the YOLO ONNX model loads.")
    verify.add_argument("--yolo-model", default=DEFAULT_MODEL)
    verify.set_defaults(func=verify_yolo)

    estimate = sub.add_parser("estimate-height", help="Estimate person height from YOLO and radar distance.")
    estimate.add_argument("--image", required=True)
    estimate.add_argument("--distance-cm", type=float, required=True, help="Radar distance to person.")
    estimate.add_argument("--calibration", default="camera_calibration_runs/latest/calibration.json")
    estimate.add_argument("--yolo-model", default=DEFAULT_MODEL)
    estimate.add_argument("--yolo-confidence", type=float, default=0.35)
    estimate.add_argument("--yolo-nms", type=float, default=0.45)
    estimate.add_argument("--yolo-image-size", type=int, default=640)
    estimate.add_argument("--person-index", type=int, default=0)
    estimate.add_argument(
        "--manual-box",
        default=None,
        help="Fallback person box as x,y,width,height when YOLO is not ready.",
    )
    estimate.set_defaults(func=estimate_height)

    live = sub.add_parser(
        "capture-height",
        help="Capture one robot-camera frame, run YOLO, and estimate height from radar distance.",
    )
    live.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    live.add_argument("--output", default="person_capture.jpg")
    live.add_argument("--jpeg-quality", type=int, default=92)
    live.add_argument("--distance-cm", type=float, required=True, help="Radar distance to person.")
    live.add_argument("--calibration", default="camera_calibration_runs/latest/calibration.json")
    live.add_argument("--yolo-model", default=DEFAULT_MODEL)
    live.add_argument("--yolo-confidence", type=float, default=0.35)
    live.add_argument("--yolo-nms", type=float, default=0.45)
    live.add_argument("--yolo-image-size", type=int, default=640)
    live.add_argument("--person-index", type=int, default=0)
    live.set_defaults(func=capture_and_estimate_height)

    auto = sub.add_parser(
        "auto-capture-height",
        help=(
            "Retry dog-camera captures until the person is fully framed; "
            "optionally move the dog using raw zsibot, not sess.motion."
        ),
    )
    auto.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    auto.add_argument("--output-dir", default="person_framing_runs/latest")
    auto.add_argument("--jpeg-quality", type=int, default=92)
    auto.add_argument("--distance-cm", type=float, required=True, help="Radar distance to person.")
    auto.add_argument("--calibration", default="camera_calibration_runs/latest/calibration.json")
    auto.add_argument("--yolo-model", default=DEFAULT_MODEL)
    auto.add_argument("--yolo-confidence", type=float, default=0.35)
    auto.add_argument("--yolo-nms", type=float, default=0.45)
    auto.add_argument("--yolo-image-size", type=int, default=640)
    auto.add_argument("--person-index", type=int, default=0)
    auto.add_argument("--max-attempts", type=int, default=5)
    auto.add_argument("--edge-margin-px", type=float, default=20.0)
    auto.add_argument("--min-person-height-ratio", type=float, default=0.35)
    auto.add_argument("--max-person-height-ratio", type=float, default=0.88)
    auto.add_argument("--center-tolerance-ratio", type=float, default=0.18)
    auto.add_argument("--settle-seconds", type=float, default=0.8)
    auto.add_argument("--execute-motion", action="store_true")
    auto.add_argument("--robot-host", default="192.168.234.1")
    auto.add_argument("--variant", default="zsl-1")
    auto.add_argument("--local-port", type=int, default=43988)
    auto.add_argument("--nudge-speed-mps", type=float, default=0.12)
    auto.add_argument("--lateral-speed-mps", type=float, default=0.08)
    auto.add_argument("--nudge-seconds", type=float, default=0.7)
    auto.set_defaults(func=auto_capture_height)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
