"""Grid and laser assisted camera calibration for the dog camera.

This is the accuracy-first path. It uses a measured wall grid and optional
laser-labeled samples to solve camera intrinsics and lens distortion.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


DEFAULT_RTSP_URL = "rtsp://192.168.234.1:8554/test"


@dataclass(frozen=True)
class GridSpec:
    rows: int
    cols: int
    square_size_cm: float
    shape: str = "l_shape"
    top_extension_rows: int = 4
    top_extension_cols: int = 2
    top_extension_start_col: int = 6

    @property
    def box_rows(self) -> int:
        return self.rows - 1

    @property
    def box_cols(self) -> int:
        return self.cols - 1

    @property
    def top_extension_end_col(self) -> int:
        return self.top_extension_start_col + self.top_extension_cols - 1

    @property
    def point_count(self) -> int:
        if self.shape == "rectangle":
            return self.rows * self.cols
        if self.shape == "l_shape":
            return self.rows * self.cols + self.top_extension_rows * (self.top_extension_cols + 1)
        raise ValueError(f"unsupported grid shape: {self.shape}")

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        if self.shape == "rectangle":
            data.pop("top_extension_rows")
            data.pop("top_extension_cols")
            data.pop("top_extension_start_col")
        return data


@dataclass(frozen=True)
class LaserDot:
    x: float
    y: float
    radius: float
    area: float


def require_cv2_numpy():
    try:
        import cv2
        import numpy as np
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "This script needs OpenCV and numpy. Install with: "
            "python3 -m pip install opencv-python-headless numpy"
        ) from exc
    return cv2, np


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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


def open_camera(rtsp_url: str):
    cv2, _ = require_cv2_numpy()
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open dog camera stream: {rtsp_url}")
    return cap


def capture_one_frame(*, rtsp_url: str, output: Path, jpeg_quality: int) -> Path:
    cv2, _ = require_cv2_numpy()
    output.parent.mkdir(parents=True, exist_ok=True)
    cap = open_camera(rtsp_url)
    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"Could not read a frame from dog camera: {rtsp_url}")
        cv2.imwrite(str(output), frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
    finally:
        cap.release()
    return output


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


def choose_evenly_spaced_lines(lines: list[float], expected_count: int) -> list[float]:
    lines = sorted(lines)
    if len(lines) <= expected_count:
        return lines

    best_subset: tuple[float, ...] | None = None
    best_score: float | None = None
    for subset in itertools.combinations(lines, expected_count):
        gaps = [subset[index + 1] - subset[index] for index in range(len(subset) - 1)]
        if not gaps or min(gaps) <= 0:
            continue
        mean_gap = sum(gaps) / len(gaps)
        spacing_error = sum((gap - mean_gap) ** 2 for gap in gaps) / len(gaps)
        span_bonus = (subset[-1] - subset[0]) * 0.01
        score = spacing_error - span_bonus
        if best_score is None or score < best_score:
            best_score = score
            best_subset = subset

    return list(best_subset or lines[:expected_count])


def split_l_shape_y_lines(y_lines: list[float], spec: GridSpec) -> tuple[list[float], list[float]] | None:
    """Return top-extension y lines and lower-rectangle y lines for the L target."""
    y_lines = sorted(y_lines)
    needed = spec.rows + spec.top_extension_rows
    if len(y_lines) < needed:
        return None

    best_top: list[float] | None = None
    best_lower: list[float] | None = None
    best_score: float | None = None
    for subset in itertools.combinations(y_lines, needed):
        gaps = [subset[index + 1] - subset[index] for index in range(len(subset) - 1)]
        if not gaps or min(gaps) <= 0:
            continue
        mean_gap = sum(gaps) / len(gaps)
        spacing_error = sum((gap - mean_gap) ** 2 for gap in gaps) / len(gaps)
        # The lower rectangle starts immediately after the top extension.
        top = list(subset[: spec.top_extension_rows])
        lower = list(subset[spec.top_extension_rows :])
        score = spacing_error - (subset[-1] - subset[0]) * 0.01
        if best_score is None or score < best_score:
            best_score = score
            best_top = top
            best_lower = lower

    if best_top is None or best_lower is None:
        return None
    return best_top, best_lower


def detect_grid_points(
    image,
    spec: GridSpec,
    *,
    blue_hue_low: int,
    blue_hue_high: int,
    min_line_length: int,
    roi: tuple[int, int, int, int] | None = None,
):
    cv2, np = require_cv2_numpy()
    working = image
    offset_x = 0
    offset_y = 0
    if roi is not None:
        x, y, width, height = roi
        image_height, image_width = image.shape[:2]
        x2 = min(image_width, x + width)
        y2 = min(image_height, y + height)
        if x < 0 or y < 0 or x >= image_width or y >= image_height or x2 <= x or y2 <= y:
            return False, None, {"reason": "roi_out_of_image", "roi": roi}
        working = image[y:y2, x:x2]
        offset_x = x
        offset_y = y

    hsv = cv2.cvtColor(working, cv2.COLOR_BGR2HSV)
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
    raw_vertical_count = len(x_lines)
    raw_horizontal_count = len(y_lines)

    needed_y_lines = spec.rows if spec.shape == "rectangle" else spec.rows
    if len(x_lines) < spec.cols or len(y_lines) < needed_y_lines:
        return (
            False,
            None,
            {
                "reason": "not_enough_grid_lines",
                "vertical_lines": raw_vertical_count,
                "horizontal_lines": raw_horizontal_count,
                "needed_vertical_lines": spec.cols,
                "needed_horizontal_lines": needed_y_lines,
            },
        )

    x_lines = choose_evenly_spaced_lines(x_lines, spec.cols)
    if spec.shape == "rectangle":
        y_lines = choose_evenly_spaced_lines(y_lines, spec.rows)
        points = np.array([[x + offset_x, y + offset_y] for y in y_lines for x in x_lines], dtype=np.float32)
        selected_y_debug = y_lines
        top_y_debug: list[float] = []
    elif spec.shape == "l_shape":
        split = split_l_shape_y_lines(y_lines, spec)
        inferred_top = False
        if split is None:
            lower_y_lines = choose_evenly_spaced_lines(y_lines, spec.rows)
            lower_gaps = [
                lower_y_lines[index + 1] - lower_y_lines[index]
                for index in range(len(lower_y_lines) - 1)
            ]
            mean_lower_gap = sum(lower_gaps) / len(lower_gaps)
            top_y_lines = [
                lower_y_lines[0] - mean_lower_gap * offset
                for offset in range(spec.top_extension_rows, 0, -1)
            ]
            inferred_top = True
        else:
            top_y_lines, lower_y_lines = split
        extension_x_lines = x_lines[
            spec.top_extension_start_col - 1 : spec.top_extension_start_col + spec.top_extension_cols
        ]
        lower_points = [[x + offset_x, y + offset_y] for y in lower_y_lines for x in x_lines]
        top_points = [[x + offset_x, y + offset_y] for y in top_y_lines for x in extension_x_lines]
        points = np.array(lower_points + top_points, dtype=np.float32)
        selected_y_debug = lower_y_lines
        top_y_debug = top_y_lines
    else:
        raise ValueError(f"unsupported grid shape: {spec.shape}")
    return True, points.reshape(-1, 1, 2), {
        "shape": spec.shape,
        "vertical_lines": len(x_lines),
        "horizontal_lines": len(selected_y_debug) + len(top_y_debug),
        "raw_vertical_lines": raw_vertical_count,
        "raw_horizontal_lines": raw_horizontal_count,
        "roi": roi,
        "top_extension_y_inferred": spec.shape == "l_shape" and inferred_top,
        "selected_x_lines": [round(value + offset_x, 2) for value in x_lines],
        "selected_lower_y_lines": [round(value + offset_y, 2) for value in selected_y_debug],
        "selected_top_extension_y_lines": [round(value + offset_y, 2) for value in top_y_debug],
    }


def object_points(spec: GridSpec):
    _, np = require_cv2_numpy()
    points = []
    for row in range(spec.rows):
        for col in range(spec.cols):
            points.append([col * spec.square_size_cm, row * spec.square_size_cm, 0.0])
    if spec.shape == "l_shape":
        for top_row in range(spec.top_extension_rows, 0, -1):
            y_cm = -top_row * spec.square_size_cm
            for offset_col in range(spec.top_extension_cols + 1):
                col = spec.top_extension_start_col - 1 + offset_col
                points.append([col * spec.square_size_cm, y_cm, 0.0])
    return np.asarray(points, dtype=np.float32)


def grid_box_center_object_point(*, spec: GridSpec, row: int, col: int, region: str = "lower"):
    _, np = require_cv2_numpy()
    if region == "lower":
        if row < 1 or row > spec.box_rows:
            raise ValueError(f"lower box row must be 1..{spec.box_rows}, got {row}")
        if col < 1 or col > spec.box_cols:
            raise ValueError(f"lower box col must be 1..{spec.box_cols}, got {col}")
        x_cm = (col - 0.5) * spec.square_size_cm
        y_cm = (row - 0.5) * spec.square_size_cm
    elif region == "top_extension":
        if spec.shape != "l_shape":
            raise ValueError("top-extension labels require --grid-shape l_shape")
        if row < 1 or row > spec.top_extension_rows:
            raise ValueError(f"top-extension row must be 1..{spec.top_extension_rows}, got {row}")
        if col < 1 or col > spec.top_extension_cols:
            raise ValueError(f"top-extension col must be 1..{spec.top_extension_cols}, got {col}")
        lower_col = spec.top_extension_start_col + col - 1
        x_cm = (lower_col - 0.5) * spec.square_size_cm
        y_cm = -(spec.top_extension_rows - row + 0.5) * spec.square_size_cm
    else:
        raise ValueError(f"unsupported box region: {region}")
    return np.asarray([[x_cm, y_cm, 0.0]], dtype=np.float32)


def parse_box_label(raw: str) -> tuple[str, int, int]:
    raw = raw.strip().lower()
    if raw.startswith("t"):
        parts = raw[1:].split(",", 1)
        if len(parts) != 2:
            raise ValueError("top-extension label must look like T1,1")
        return "top_extension", int(parts[0].strip()), int(parts[1].strip())
    parts = raw.split(",", 1)
    if len(parts) != 2:
        raise ValueError("lower-grid label must look like 1,1")
    return "lower", int(parts[0].strip()), int(parts[1].strip())


def format_box_label(region: str, row: int, col: int) -> str:
    if region == "top_extension":
        return f"T{row},{col}"
    return f"{row},{col}"


def parse_roi(raw: str | None) -> tuple[int, int, int, int] | None:
    if not raw:
        return None
    parts = [int(part.strip()) for part in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("--roi must be x,y,width,height")
    x, y, width, height = parts
    if width <= 0 or height <= 0:
        raise ValueError("--roi width and height must be positive")
    return x, y, width, height


def dot_inside_labeled_box(
    *,
    spec: GridSpec,
    grid_debug: dict[str, object],
    dot: LaserDot,
    region: str,
    row: int,
    col: int,
    margin_px: float = 12.0,
) -> dict[str, object]:
    x_lines = [float(value) for value in grid_debug.get("selected_x_lines", [])]
    lower_y_lines = [float(value) for value in grid_debug.get("selected_lower_y_lines", [])]
    top_y_lines = [float(value) for value in grid_debug.get("selected_top_extension_y_lines", [])]

    if region == "lower":
        grid_box_center_object_point(spec=spec, row=row, col=col, region=region)
        if len(x_lines) < col + 1 or len(lower_y_lines) < row + 1:
            return {"box_check": "unknown", "reason": "missing_lower_grid_lines"}
        left, right = x_lines[col - 1], x_lines[col]
        top, bottom = lower_y_lines[row - 1], lower_y_lines[row]
    elif region == "top_extension":
        grid_box_center_object_point(spec=spec, row=row, col=col, region=region)
        start_index = spec.top_extension_start_col - 1
        left_index = start_index + col - 1
        right_index = left_index + 1
        if len(x_lines) <= right_index or len(top_y_lines) < row + 1:
            return {"box_check": "unknown", "reason": "missing_top_extension_grid_lines"}
        left, right = x_lines[left_index], x_lines[right_index]
        top, bottom = top_y_lines[row - 1], top_y_lines[row]
    else:
        return {"box_check": "unknown", "reason": f"unsupported_region:{region}"}

    inside = (
        min(left, right) - margin_px <= dot.x <= max(left, right) + margin_px
        and min(top, bottom) - margin_px <= dot.y <= max(top, bottom) + margin_px
    )
    return {
        "box_check": "inside" if inside else "outside",
        "margin_px": margin_px,
        "expected_box_bounds_px": {
            "left": round(left, 2),
            "right": round(right, 2),
            "top": round(top, 2),
            "bottom": round(bottom, 2),
        },
        "laser_dot_px": {"x": round(dot.x, 2), "y": round(dot.y, 2)},
    }


def box_check_summary(box_check: dict[str, object]) -> str:
    dot = box_check.get("laser_dot_px")
    bounds = box_check.get("expected_box_bounds_px")
    if not isinstance(dot, dict) or not isinstance(bounds, dict):
        return f"reason={box_check.get('reason', 'none')}"
    return (
        f"dot=({dot.get('x')},{dot.get('y')}) "
        f"bounds=left:{bounds.get('left')} right:{bounds.get('right')} "
        f"top:{bounds.get('top')} bottom:{bounds.get('bottom')}"
    )


def make_grid_spec(args: argparse.Namespace) -> GridSpec:
    return GridSpec(
        rows=args.grid_rows,
        cols=args.grid_cols,
        square_size_cm=args.square_size_cm,
        shape=args.grid_shape,
        top_extension_rows=args.top_extension_rows,
        top_extension_cols=args.top_extension_cols,
        top_extension_start_col=args.top_extension_start_col,
    )


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


def inspect_grid(args: argparse.Namespace) -> None:
    cv2, _ = require_cv2_numpy()
    image = cv2.imread(str(args.image))
    if image is None:
        raise RuntimeError(f"OpenCV could not read image: {args.image}")
    found, points, debug = detect_grid_points(
        image,
        make_grid_spec(args),
        blue_hue_low=args.blue_hue_low,
        blue_hue_high=args.blue_hue_high,
        min_line_length=args.min_line_length,
        roi=parse_roi(args.roi),
    )
    print(f"grid_found={str(found).lower()}")
    print(f"point_count={0 if points is None else len(points)}")
    print(json.dumps(debug, indent=2))


def capture_grid(args: argparse.Namespace) -> None:
    cv2, _ = require_cv2_numpy()
    spec = make_grid_spec(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cap = open_camera(args.rtsp_url)
    saved = 0
    accepted = 0
    records: list[dict[str, object]] = []
    next_capture_at = time.monotonic()
    started_at = datetime.now().isoformat(timespec="seconds")

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
                spec,
                blue_hue_low=args.blue_hue_low,
                blue_hue_high=args.blue_hue_high,
                min_line_length=args.min_line_length,
                roi=parse_roi(args.roi),
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
                print(f"saved={saved} accepted={accepted} last_grid_found={str(found).lower()}")
    finally:
        cap.release()

    save_json(
        output_dir / "capture_records.json",
        {
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "saved_count": saved,
            "accepted_count": accepted,
            "grid": spec.as_dict(),
            "records": records,
        },
    )
    print(f"capture_complete=true saved={saved} accepted={accepted}")


def capture_laser_samples(args: argparse.Namespace) -> None:
    cv2, _ = require_cv2_numpy()
    spec = make_grid_spec(args)
    output_dir = Path(args.output_dir)
    samples_path = Path(args.samples)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"laser_samples={samples_path}")
    print(f"grid_shape={spec.shape}")
    print(f"lower_grid_boxes={spec.box_rows}x{spec.box_cols}")
    if spec.shape == "l_shape":
        print(
            "top_extension_boxes="
            f"{spec.top_extension_rows}x{spec.top_extension_cols} "
            f"above_lower_columns={spec.top_extension_start_col}..{spec.top_extension_end_col}"
        )
    print("lower_box_labels_are=1_based_from_top_left_like_1,1")
    print("top_extension_labels_are=Trow,col_like_T1,1")

    accepted_count = 0
    attempt = 0
    while accepted_count < args.count:
        attempt += 1
        if args.interactive:
            raw = input(f"sample {accepted_count + 1}/{args.count} box row,col (or q)> ").strip().lower()
            if raw in {"q", "quit", "exit"}:
                break
            region, row, col = parse_box_label(raw)
        else:
            if args.box_row is None or args.box_col is None:
                raise RuntimeError("Use --interactive or provide --box-row and --box-col.")
            region, row, col = args.box_region, args.box_row, args.box_col

        grid_box_center_object_point(spec=spec, row=row, col=col, region=region)
        image_path = output_dir / f"laser_{int(time.time())}_{attempt:04d}.jpg"
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
            roi=parse_roi(args.roi),
        )
        box_check = {"box_check": "unknown", "reason": "laser_or_grid_missing"}
        sample_accepted = False
        if dot is not None and grid_found:
            box_check = dot_inside_labeled_box(
                spec=spec,
                grid_debug=grid_debug,
                dot=dot,
                region=region,
                row=row,
                col=col,
                margin_px=args.box_margin_px,
            )
            sample_accepted = box_check.get("box_check") == "inside"
        sample = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "image": str(image_path),
            "box_region": region,
            "box_row": row,
            "box_col": col,
            "box_label": format_box_label(region, row, col),
            "box_origin": "top_left",
            "grid": spec.as_dict(),
            "laser_color": args.laser_color,
            "laser_detected": dot is not None,
            "laser_dot": None if dot is None else asdict(dot),
            "grid_found": grid_found,
            "grid_point_count": 0 if grid_points is None else int(len(grid_points)),
            "laser_debug": laser_debug,
            "grid_debug": grid_debug,
            "box_check": box_check,
            "sample_accepted": sample_accepted,
            "label": args.label,
        }
        status = "accepted" if sample_accepted else "rejected"
        if sample_accepted or args.save_rejected:
            append_jsonl(samples_path, sample)
            print(
                f"sample_{status}={image_path} box={format_box_label(region, row, col)} "
                f"accepted_count={accepted_count + int(sample_accepted)}/{args.count} "
                f"laser_detected={str(dot is not None).lower()} grid_found={str(grid_found).lower()} "
                f"box_check={box_check.get('box_check')} {box_check_summary(box_check)}"
            )
        else:
            image_path.unlink(missing_ok=True)
            print(
                f"sample_rejected=not_saved box={format_box_label(region, row, col)} "
                f"accepted_count={accepted_count}/{args.count} "
                f"laser_detected={str(dot is not None).lower()} grid_found={str(grid_found).lower()} "
                f"box_check={box_check.get('box_check')} {box_check_summary(box_check)} "
                f"grid_reason={grid_debug.get('reason', 'none')}"
            )
        if sample_accepted:
            accepted_count += 1


def calibrate_from_images(args: argparse.Namespace) -> None:
    cv2, np = require_cv2_numpy()
    spec = make_grid_spec(args)
    image_dir = Path(args.image_dir)
    images = sorted(image_dir.glob("*.jpg")) + sorted(image_dir.glob("*.png"))
    if not images:
        raise RuntimeError(f"No calibration images found in {image_dir}")

    object_template = object_points(spec)
    object_sets = []
    image_sets = []
    accepted: list[str] = []
    rejected: list[dict[str, object]] = []
    image_size: tuple[int, int] | None = None

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
            roi=parse_roi(args.roi),
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
        raise RuntimeError(f"Need at least {args.min_accepted} accepted grid images, got {len(accepted)}.")

    rms, camera_matrix, distortion, _rvecs, _tvecs = cv2.calibrateCamera(
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
        "source": "grid_images",
        "image_dir": str(image_dir),
        "image_width": image_size[0],
        "image_height": image_size[1],
        "grid": spec.as_dict(),
        "attempt_count": len(images),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "rms_reprojection_error": float(rms),
        "camera_matrix": camera_matrix.tolist(),
        "distortion_coefficients": distortion.ravel().tolist(),
        "accepted_images": accepted,
        "rejected_images": rejected,
    }
    save_json(Path(args.output), report)
    print(f"calibration_saved={args.output}")
    print(f"accepted_count={len(accepted)}")
    print(f"rms_reprojection_error={float(rms):.6f}")


def calibrate_from_laser_samples(args: argparse.Namespace) -> None:
    cv2, np = require_cv2_numpy()
    spec = make_grid_spec(args)
    samples = load_jsonl(Path(args.samples))
    if not samples:
        raise RuntimeError(f"No laser samples found: {args.samples}")

    base_object_points = object_points(spec).reshape(-1, 3)
    object_sets = []
    image_sets = []
    accepted: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    image_size: tuple[int, int] | None = None

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
            roi=parse_roi(args.roi),
        )
        if not found or grid_points is None:
            rejected.append({"image": str(image_path), **grid_debug})
            continue
        laser_dot = sample.get("laser_dot")
        if not isinstance(laser_dot, dict):
            rejected.append({"image": str(image_path), "reason": "laser_dot_missing"})
            continue

        region = str(sample.get("box_region", "lower"))
        row = int(sample["box_row"])
        col = int(sample["box_col"])
        dot = LaserDot(
            x=float(laser_dot["x"]),
            y=float(laser_dot["y"]),
            radius=float(laser_dot.get("radius", 0.0)),
            area=float(laser_dot.get("area", 0.0)),
        )
        box_check = dot_inside_labeled_box(
            spec=spec,
            grid_debug=grid_debug,
            dot=dot,
            region=region,
            row=row,
            col=col,
        )
        if box_check.get("box_check") != "inside":
            rejected.append(
                {
                    "image": str(image_path),
                    "reason": "laser_dot_outside_labeled_box",
                    "box_label": format_box_label(region, row, col),
                    "box_check": box_check,
                }
            )
            continue
        laser_object = grid_box_center_object_point(spec=spec, row=row, col=col, region=region)
        laser_image = np.asarray([[[float(laser_dot["x"]), float(laser_dot["y"])]]], dtype=np.float32)
        object_set = np.vstack([base_object_points, laser_object.reshape(-1, 3)])
        image_set = np.vstack([grid_points.reshape(-1, 2), laser_image.reshape(-1, 2)]).reshape(-1, 1, 2)
        object_sets.append(object_set.astype(np.float32))
        image_sets.append(image_set.astype(np.float32))
        accepted.append(
            {
                "image": str(image_path),
                "box_region": region,
                "box_row": row,
                "box_col": col,
                "box_label": format_box_label(region, row, col),
            }
        )

    if image_size is None:
        raise RuntimeError("OpenCV could not read any laser sample image.")
    if len(accepted) < args.min_accepted:
        raise RuntimeError(f"Need at least {args.min_accepted} accepted laser samples, got {len(accepted)}.")

    flags = cv2.CALIB_FIX_PRINCIPAL_POINT if args.fix_principal_point else 0
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
    grid_count = spec.point_count
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
        "grid": spec.as_dict(),
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_grid_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--grid-shape", choices=["rectangle", "l_shape"], default="l_shape")
        p.add_argument("--grid-rows", type=int, default=8)
        p.add_argument("--grid-cols", type=int, default=8)
        p.add_argument("--square-size-cm", type=float, default=15.0)
        p.add_argument("--top-extension-rows", type=int, default=4)
        p.add_argument("--top-extension-cols", type=int, default=2)
        p.add_argument("--top-extension-start-col", type=int, default=6)
        p.add_argument(
            "--roi",
            default=None,
            help="Optional grid search crop as x,y,width,height. Use this when other blue lines are visible.",
        )
        p.add_argument("--blue-hue-low", type=int, default=85)
        p.add_argument("--blue-hue-high", type=int, default=135)
        p.add_argument("--min-line-length", type=int, default=80)

    def add_laser_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--laser-color", choices=["red", "green"], default="red")
        p.add_argument("--laser-min-area", type=float, default=3.0)
        p.add_argument("--laser-max-area", type=float, default=1800.0)

    inspect = sub.add_parser("inspect-grid", help="Check grid detection in one image.")
    inspect.add_argument("--image", default="test_camera.jpg")
    add_grid_args(inspect)
    inspect.set_defaults(func=inspect_grid)

    capture = sub.add_parser("capture-grid", help="Capture many dog-camera grid images.")
    capture.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    capture.add_argument("--output-dir", default="camera_calibration_runs/latest/images")
    capture.add_argument("--count", type=int, default=200)
    capture.add_argument("--interval-sec", type=float, default=0.1)
    capture.add_argument("--jpeg-quality", type=int, default=92)
    capture.add_argument("--progress-every", type=int, default=25)
    add_grid_args(capture)
    capture.set_defaults(func=capture_grid)

    laser = sub.add_parser("capture-laser-samples", help="Capture laser samples labeled by grid box.")
    laser.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    laser.add_argument("--output-dir", default="camera_calibration_runs/latest/laser_images")
    laser.add_argument("--samples", default="camera_calibration_runs/latest/laser_samples.jsonl")
    laser.add_argument("--count", type=int, default=50)
    laser.add_argument("--interactive", action="store_true")
    laser.add_argument("--box-region", choices=["lower", "top_extension"], default="lower")
    laser.add_argument("--box-row", type=int, default=None)
    laser.add_argument("--box-col", type=int, default=None)
    laser.add_argument("--label", default="")
    laser.add_argument("--save-rejected", action="store_true")
    laser.add_argument("--box-margin-px", type=float, default=12.0)
    laser.add_argument("--jpeg-quality", type=int, default=92)
    add_laser_args(laser)
    add_grid_args(laser)
    laser.set_defaults(func=capture_laser_samples)

    calibrate = sub.add_parser("calibrate", help="Calibrate from captured grid images.")
    calibrate.add_argument("--image-dir", default="camera_calibration_runs/latest/images")
    calibrate.add_argument("--output", default="camera_calibration_runs/latest/calibration.json")
    calibrate.add_argument("--min-accepted", type=int, default=30)
    add_grid_args(calibrate)
    calibrate.set_defaults(func=calibrate_from_images)

    laser_calibrate = sub.add_parser("calibrate-laser", help="Calibrate from laser-labeled grid samples.")
    laser_calibrate.add_argument("--samples", default="camera_calibration_runs/latest/laser_samples.jsonl")
    laser_calibrate.add_argument("--output", default="camera_calibration_runs/latest/calibration.json")
    laser_calibrate.add_argument("--min-accepted", type=int, default=10)
    laser_calibrate.add_argument("--fix-principal-point", action="store_true")
    add_grid_args(laser_calibrate)
    laser_calibrate.set_defaults(func=calibrate_from_laser_samples)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
