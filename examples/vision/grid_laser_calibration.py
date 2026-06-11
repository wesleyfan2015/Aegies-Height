"""Grid and laser assisted camera calibration for the dog camera.

This is the accuracy-first path. It uses a measured wall grid and optional
laser-labeled samples to solve camera intrinsics and lens distortion.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from multiprocessing import Process, Queue
from pathlib import Path


DEFAULT_RTSP_URL = "rtsp://192.168.234.1:8554/test"
DEFAULT_FFMPEG_CAPTURE_OPTIONS = "rtsp_transport;tcp|stimeout;3000000|rw_timeout;3000000|max_delay;500000"


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


def prune_old_files(directory: Path, pattern: str, keep: int) -> None:
    if keep <= 0 or not directory.exists():
        return
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files[keep:]:
        path.unlink(missing_ok=True)


def write_image_or_raise(path: Path, image, jpeg_quality: int | None = None) -> None:
    cv2, _ = require_cv2_numpy()
    path.parent.mkdir(parents=True, exist_ok=True)
    params = [] if jpeg_quality is None else [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
    ok = cv2.imwrite(str(path), image, params)
    if not ok or not path.exists():
        raise RuntimeError(f"Could not write image: {path}")


def log_step(message: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


def open_camera(rtsp_url: str):
    cv2, _ = require_cv2_numpy()
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", DEFAULT_FFMPEG_CAPTURE_OPTIONS)
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open dog camera stream: {rtsp_url}")
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
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


def _capture_one_frame_worker(rtsp_url: str, output: str, jpeg_quality: int, queue) -> None:
    try:
        capture_one_frame(rtsp_url=rtsp_url, output=Path(output), jpeg_quality=jpeg_quality)
        queue.put({"ok": True})
    except Exception as exc:
        queue.put({"ok": False, "error": str(exc)})


def capture_one_frame_with_timeout(
    *,
    rtsp_url: str,
    output: Path,
    jpeg_quality: int,
    timeout_sec: float,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    queue = Queue()
    process = Process(
        target=_capture_one_frame_worker,
        args=(rtsp_url, str(output), jpeg_quality, queue),
    )
    process.start()
    process.join(timeout=max(0.5, timeout_sec))
    if process.is_alive():
        process.terminate()
        process.join(1.0)
        raise RuntimeError(f"Timed out reading dog camera after {timeout_sec:.1f}s: {rtsp_url}")
    result = queue.get() if not queue.empty() else {"ok": False, "error": "capture worker exited without result"}
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error", "camera capture failed")))
    if not output.exists():
        raise RuntimeError(f"Camera capture did not create image: {output}")
    return output


def read_camera_frame(cap, *, reconnect_url: str | None = None):
    ok, frame = cap.read()
    if ok and frame is not None:
        return cap, frame
    if reconnect_url is None:
        raise RuntimeError("Could not read a frame from camera.")
    cap.release()
    time.sleep(0.25)
    cap = open_camera(reconnect_url)
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError(f"Could not read a frame from dog camera: {reconnect_url}")
    return cap, frame


def flush_camera_frames(cap, count: int, *, reconnect_url: str):
    for _ in range(max(0, count)):
        cap, _frame = read_camera_frame(cap, reconnect_url=reconnect_url)
    return cap


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


def filter_x_lines_by_y_overlap(
    x_lines: list[float],
    vertical_segments: list[tuple[float, float, float]],
    y_lines: list[float],
    *,
    x_tolerance: float = 22.0,
    min_overlap_fraction: float = 0.45,
) -> list[float]:
    """Keep vertical grid lines that actually run through the detected lower grid rows."""
    if len(y_lines) < 2:
        return x_lines

    top = min(y_lines)
    bottom = max(y_lines)
    grid_height = max(1.0, bottom - top)
    min_overlap_px = grid_height * min_overlap_fraction
    filtered: list[float] = []

    for x_line in sorted(x_lines):
        overlap = 0.0
        for x_mid, y1, y2 in vertical_segments:
            if abs(x_mid - x_line) > x_tolerance:
                continue
            segment_top = max(min(y1, y2), top)
            segment_bottom = min(max(y1, y2), bottom)
            if segment_bottom > segment_top:
                overlap += segment_bottom - segment_top
        if overlap >= min_overlap_px:
            filtered.append(x_line)

    return filtered


def detect_grid_points(
    image,
    spec: GridSpec,
    *,
    blue_hue_low: int,
    blue_hue_high: int,
    min_line_length: int,
    hough_threshold: int,
    skip_left_x_lines: int,
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
        threshold=hough_threshold,
        minLineLength=min_line_length,
        maxLineGap=25,
    )
    if lines is None:
        return False, None, {"reason": "no_blue_grid_lines"}

    vertical_x: list[float] = []
    vertical_segments: list[tuple[float, float, float]] = []
    horizontal_y: list[float] = []
    for raw in lines.reshape(-1, 4):
        x1, y1, x2, y2 = [float(v) for v in raw]
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length < min_line_length:
            continue
        if abs(dx) < max(12.0, abs(dy) * 0.25):
            x_mid = (x1 + x2) / 2.0
            vertical_x.append(x_mid)
            vertical_segments.append((x_mid, min(y1, y2), max(y1, y2)))
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

    filtered_x_lines = x_lines
    lower_x_filter_used = False
    if spec.shape == "rectangle":
        y_lines = choose_evenly_spaced_lines(y_lines, spec.rows)
        filtered_x_lines = filter_x_lines_by_y_overlap(x_lines, vertical_segments, y_lines)
        lower_x_filter_used = len(filtered_x_lines) >= spec.cols
        if lower_x_filter_used:
            x_lines = filtered_x_lines
        if skip_left_x_lines > 0:
            x_lines = sorted(x_lines)[skip_left_x_lines:]
        x_lines = choose_evenly_spaced_lines(x_lines, spec.cols)
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
        filtered_x_lines = filter_x_lines_by_y_overlap(x_lines, vertical_segments, lower_y_lines)
        lower_x_filter_used = len(filtered_x_lines) >= spec.cols
        if lower_x_filter_used:
            x_lines = filtered_x_lines
        if skip_left_x_lines > 0:
            x_lines = sorted(x_lines)[skip_left_x_lines:]
        x_lines = choose_evenly_spaced_lines(x_lines, spec.cols)
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
        "filtered_vertical_lines": len(filtered_x_lines),
        "lower_x_filter_used": lower_x_filter_used,
        "skip_left_x_lines": skip_left_x_lines,
        "hough_threshold": hough_threshold,
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


def clamp_roi_to_image(
    roi: tuple[int, int, int, int],
    *,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    x, y, width, height = roi
    left = max(0, min(image_width - 1, x))
    top = max(0, min(image_height - 1, y))
    right = max(left + 1, min(image_width, x + width))
    bottom = max(top + 1, min(image_height, y + height))
    return left, top, right - left, bottom - top


def roi_from_grid_points(
    grid_points,
    *,
    image_width: int,
    image_height: int,
    padding_px: int = 20,
) -> tuple[int, int, int, int]:
    _, np = require_cv2_numpy()
    points = np.asarray(grid_points, dtype=np.float32).reshape(-1, 2)
    min_x = int(math.floor(float(points[:, 0].min()))) - padding_px
    max_x = int(math.ceil(float(points[:, 0].max()))) + padding_px
    min_y = int(math.floor(float(points[:, 1].min()))) - padding_px
    max_y = int(math.ceil(float(points[:, 1].max()))) + padding_px
    return clamp_roi_to_image(
        (min_x, min_y, max_x - min_x, max_y - min_y),
        image_width=image_width,
        image_height=image_height,
    )


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
    suggested_box = suggested_box_for_dot(
        spec=spec,
        x_lines=x_lines,
        lower_y_lines=lower_y_lines,
        top_y_lines=top_y_lines,
        dot=dot,
        margin_px=margin_px,
    )

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
    typed_box = format_box_label(region, row, col)
    if inside and suggested_box and suggested_box != typed_box:
        inside = False
    return {
        "box_check": "inside" if inside else "outside",
        "reason": None if inside else ("closest_box_mismatch" if suggested_box and suggested_box != typed_box else "outside_bounds"),
        "margin_px": margin_px,
        "expected_box_bounds_px": {
            "left": round(left, 2),
            "right": round(right, 2),
            "top": round(top, 2),
            "bottom": round(bottom, 2),
        },
        "laser_dot_px": {"x": round(dot.x, 2), "y": round(dot.y, 2)},
        "suggested_box": suggested_box,
    }


def interval_index(value: float, lines: list[float], *, margin_px: float = 0.0) -> int | None:
    for index in range(len(lines) - 1):
        left = min(lines[index], lines[index + 1]) - margin_px
        right = max(lines[index], lines[index + 1]) + margin_px
        if left <= value <= right:
            return index
    return None


def suggested_box_for_dot(
    *,
    spec: GridSpec,
    x_lines: list[float],
    lower_y_lines: list[float],
    top_y_lines: list[float],
    dot: LaserDot,
    margin_px: float,
) -> str | None:
    x_index = interval_index(dot.x, x_lines, margin_px=margin_px)
    if x_index is None:
        return None

    lower_y_index = interval_index(dot.y, lower_y_lines, margin_px=margin_px)
    if lower_y_index is not None:
        row = lower_y_index + 1
        col = x_index + 1
        if 1 <= row <= spec.box_rows and 1 <= col <= spec.box_cols:
            return format_box_label("lower", row, col)

    top_y_index = interval_index(dot.y, top_y_lines, margin_px=margin_px)
    if top_y_index is not None and spec.shape == "l_shape":
        start_index = spec.top_extension_start_col - 1
        top_col = x_index - start_index + 1
        row = top_y_index + 1
        if 1 <= row <= spec.top_extension_rows and 1 <= top_col <= spec.top_extension_cols:
            return format_box_label("top_extension", row, top_col)

    return None


def box_check_summary(box_check: dict[str, object]) -> str:
    dot = box_check.get("laser_dot_px")
    bounds = box_check.get("expected_box_bounds_px")
    if not isinstance(dot, dict) or not isinstance(bounds, dict):
        return f"reason={box_check.get('reason', 'none')}"
    suggestion = box_check.get("suggested_box")
    suggestion_text = f" suggested_box={suggestion}" if suggestion else ""
    return (
        f"dot=({dot.get('x')},{dot.get('y')}) "
        f"bounds=left:{bounds.get('left')} right:{bounds.get('right')} "
        f"top:{bounds.get('top')} bottom:{bounds.get('bottom')}"
        f"{suggestion_text}"
    )


def save_debug_overlay(
    *,
    image,
    output: Path,
    grid_debug: dict[str, object],
    dot: LaserDot | None,
    box_check: dict[str, object],
    label: str,
) -> None:
    cv2, _ = require_cv2_numpy()
    overlay = image.copy()

    roi = grid_debug.get("roi")
    if isinstance(roi, (list, tuple)) and len(roi) == 4:
        x, y, width, height = [int(value) for value in roi]
        cv2.rectangle(overlay, (x, y), (x + width, y + height), (255, 255, 0), 2)

    height, width = overlay.shape[:2]
    for x_value in grid_debug.get("selected_x_lines", []):
        x = int(round(float(x_value)))
        cv2.line(overlay, (x, 0), (x, height - 1), (255, 0, 0), 1)
    for y_value in grid_debug.get("selected_lower_y_lines", []):
        y = int(round(float(y_value)))
        cv2.line(overlay, (0, y), (width - 1, y), (0, 255, 255), 1)
    for y_value in grid_debug.get("selected_top_extension_y_lines", []):
        y = int(round(float(y_value)))
        cv2.line(overlay, (0, y), (width - 1, y), (255, 255, 0), 1)

    bounds = box_check.get("expected_box_bounds_px")
    if isinstance(bounds, dict):
        left = int(round(float(bounds["left"])))
        right = int(round(float(bounds["right"])))
        top = int(round(float(bounds["top"])))
        bottom = int(round(float(bounds["bottom"])))
        cv2.rectangle(overlay, (left, top), (right, bottom), (0, 255, 0), 3)

    if dot is not None:
        center = (int(round(dot.x)), int(round(dot.y)))
        cv2.circle(overlay, center, 10, (0, 0, 255), 3)
        cv2.circle(overlay, center, 2, (255, 255, 255), -1)

    text = f"typed={label} check={box_check.get('box_check')}"
    suggestion = box_check.get("suggested_box")
    if suggestion:
        text += f" suggested={suggestion}"
    cv2.putText(overlay, text, (24, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(overlay, text, (24, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)

    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), overlay)


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


def load_grid_reference(path: str | None):
    if not path:
        return None
    _, np = require_cv2_numpy()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    points = np.asarray(data["grid_points"], dtype=np.float32).reshape(-1, 1, 2)
    return {
        "path": path,
        "grid_debug": data["grid_debug"],
        "grid_points": points,
        "image_width": int(data["image_width"]),
        "image_height": int(data["image_height"]),
    }


def capture_grid_reference(args: argparse.Namespace) -> None:
    cv2, _ = require_cv2_numpy()
    spec = make_grid_spec(args)
    image_path = Path(args.image_output)
    output_path = Path(args.output)
    if args.image:
        source_path = Path(args.image)
        image = cv2.imread(str(source_path))
        if image is None:
            raise RuntimeError(f"OpenCV could not read image: {source_path}")
        image_path.parent.mkdir(parents=True, exist_ok=True)
        write_image_or_raise(image_path, image, args.jpeg_quality)
        print(f"grid_reference_source_image={source_path.resolve()}", flush=True)
    else:
        log_step(f"grid_reference_capture_timeout_sec={args.capture_timeout_sec}")
        capture_one_frame_with_timeout(
            rtsp_url=args.rtsp_url,
            output=image_path,
            jpeg_quality=args.jpeg_quality,
            timeout_sec=args.capture_timeout_sec,
        )
        log_step("grid_reference_frame_read")
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"OpenCV could not read captured image: {image_path}")

    requested_roi = parse_roi(args.roi)
    found, points, debug = detect_grid_points(
        image,
        spec,
        blue_hue_low=args.blue_hue_low,
        blue_hue_high=args.blue_hue_high,
        min_line_length=args.min_line_length,
        hough_threshold=args.hough_threshold,
        skip_left_x_lines=args.skip_left_x_lines,
        roi=requested_roi,
    )
    if not found and requested_roi is not None:
        print(f"grid_reference_roi_failed={json.dumps(debug)}")
        print("grid_reference_retrying_without_roi=false")
    if not found or points is None:
        failure_path = output_path.with_name(output_path.stem + "_failed_image.jpg")
        write_image_or_raise(failure_path, image, args.jpeg_quality)
        print(f"grid_reference_failed_image={failure_path.resolve()}")
        raise RuntimeError(f"Grid reference failed: {json.dumps(debug)}")

    height, width = image.shape[:2]
    save_json(
        output_path,
        {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source": "grid_reference",
            "image": str(image_path),
            "image_width": width,
            "image_height": height,
            "grid": spec.as_dict(),
            "grid_point_count": int(len(points)),
            "grid_points": points.reshape(-1, 2).tolist(),
            "grid_debug": debug,
        },
    )
    print(f"grid_reference_saved={output_path.resolve()}")
    print(f"grid_reference_image={image_path.resolve()}")
    print(f"grid_found=true point_count={len(points)}")


def detect_laser_dot(
    image,
    *,
    color: str,
    min_area: float,
    max_area: float,
    roi: tuple[int, int, int, int] | None = None,
    min_saturation: int = 35,
    min_value: int = 45,
    min_green_dominance: int = 25,
) -> tuple[LaserDot | None, dict[str, object]]:
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
            return None, {"reason": "roi_out_of_image", "roi": roi}
        working = image[y:y2, x:x2]
        offset_x = x
        offset_y = y

    hsv = cv2.cvtColor(working, cv2.COLOR_BGR2HSV)
    if color == "red":
        mask1 = cv2.inRange(hsv, np.array([0, min_saturation, min_value]), np.array([16, 255, 255]))
        mask2 = cv2.inRange(hsv, np.array([162, min_saturation, min_value]), np.array([179, 255, 255]))
        mask = cv2.bitwise_or(mask1, mask2)
        score_image = hsv[:, :, 2].astype(np.float32)
    elif color == "green":
        # The real laser often appears cyan/blue-green on the dog camera sensor,
        # so accept green through cyan hues while still rejecting white/gray glare
        # by requiring saturation and color dominance over red.
        hsv_mask = cv2.inRange(hsv, np.array([35, min_saturation, min_value]), np.array([110, 255, 255]))
        blue, green, red = cv2.split(working)
        cyan_green = np.maximum(green, blue).astype(np.int16)
        red_rejection = cyan_green - red.astype(np.int16)
        dominance_mask = cv2.inRange(red_rejection, int(min_green_dominance), 255)
        bright_mask = cv2.inRange(cyan_green.astype(np.uint8), int(min_value), 255)
        green_excess = green.astype(np.int16) - np.maximum(red, blue).astype(np.int16)
        green_excess_mask = cv2.inRange(green_excess, max(4, int(min_green_dominance // 2)), 255)
        green_bright_mask = cv2.inRange(green, int(min_value), 255)
        green_only_mask = cv2.bitwise_and(green_excess_mask, green_bright_mask)
        mask = cv2.bitwise_or(
            cv2.bitwise_and(hsv_mask, cv2.bitwise_and(dominance_mask, bright_mask)),
            green_only_mask,
        )
        score_image = green.astype(np.float32)
    else:
        raise ValueError("laser color must be red or green")

    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, LaserDot]] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < min_area or area > max_area:
            continue
        (_, _), radius = cv2.minEnclosingCircle(contour)
        component_mask = np.zeros(mask.shape, dtype=np.uint8)
        cv2.drawContours(component_mask, [contour], -1, 255, thickness=-1)
        _min_value, peak_value, _min_loc, peak_loc = cv2.minMaxLoc(score_image, mask=component_mask)
        if peak_value <= 0:
            continue
        peak_threshold = peak_value * 0.85
        core_mask = (component_mask > 0) & (score_image >= peak_threshold)
        core_y, core_x = np.where(core_mask)
        if len(core_x) == 0:
            x = float(peak_loc[0])
            y = float(peak_loc[1])
        else:
            weights = score_image[core_y, core_x].astype(np.float64)
            x = float(np.average(core_x, weights=weights))
            y = float(np.average(core_y, weights=weights))
        x_int = max(0, min(mask.shape[1] - 1, int(round(x))))
        y_int = max(0, min(mask.shape[0] - 1, int(round(y))))
        brightness = float(hsv[y_int, x_int, 2])
        candidates.append(
            (
                brightness * area,
                LaserDot(x=x + offset_x, y=y + offset_y, radius=float(radius), area=area),
            )
        )

    if not candidates:
        return None, {
            "reason": "laser_not_detected",
            "contours": len(contours),
            "roi": roi,
            "min_saturation": min_saturation,
            "min_value": min_value,
            "min_green_dominance": min_green_dominance,
        }
    score, dot = max(candidates, key=lambda item: item[0])
    return dot, {"laser_candidates": len(candidates), "laser_score": round(score, 2), "roi": roi}


def inspect_laser(args: argparse.Namespace) -> None:
    cv2, _ = require_cv2_numpy()
    image_path = Path(args.output)
    grid_reference = load_grid_reference(args.grid_reference)
    if args.image:
        source_path = Path(args.image)
        image = cv2.imread(str(source_path))
        if image is None:
            raise RuntimeError(f"OpenCV could not read image: {source_path}")
        write_image_or_raise(image_path, image, args.jpeg_quality)
    else:
        if args.warmup_frames > 0:
            log_step("inspect_laser_warmup_disabled_for_timeout_capture")
        log_step(f"inspect_laser_capture_timeout_sec={args.capture_timeout_sec}")
        capture_one_frame_with_timeout(
            rtsp_url=args.rtsp_url,
            output=image_path,
            jpeg_quality=args.jpeg_quality,
            timeout_sec=args.capture_timeout_sec,
        )
        log_step("inspect_laser_frame_read")
        image = cv2.imread(str(image_path))
        if image is None:
            raise RuntimeError(f"OpenCV could not read captured image: {image_path}")

    height, width = image.shape[:2]
    requested_roi = parse_roi(args.roi)
    laser_roi = requested_roi
    if laser_roi is None and grid_reference is not None:
        laser_roi = roi_from_grid_points(
            grid_reference["grid_points"],
            image_width=width,
            image_height=height,
            padding_px=args.wall_padding_px,
        )
        print(f"wall_roi_from_grid_reference={laser_roi}")

    dot, debug = detect_laser_dot(
        image,
        color=args.laser_color,
        min_area=args.laser_min_area,
        max_area=args.laser_max_area,
        roi=laser_roi,
        min_saturation=args.laser_min_saturation,
        min_value=args.laser_min_value,
        min_green_dominance=args.laser_min_green_dominance,
    )
    overlay = image.copy()
    roi = laser_roi
    if roi is not None:
        x, y, width, height = roi
        cv2.rectangle(overlay, (x, y), (x + width, y + height), (255, 255, 0), 2)
    if dot is not None:
        center = (int(round(dot.x)), int(round(dot.y)))
        cv2.circle(overlay, center, 12, (0, 0, 255), 3)
        cv2.circle(overlay, center, 3, (255, 255, 255), -1)
    status = f"laser_detected={str(dot is not None).lower()}"
    cv2.putText(overlay, status, (24, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(overlay, status, (24, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    write_image_or_raise(Path(args.debug_output), overlay, args.jpeg_quality)

    print(status)
    print(f"image={image_path.resolve()}")
    print(f"debug_image={Path(args.debug_output).resolve()}")
    print(json.dumps({"laser_dot": None if dot is None else asdict(dot), "debug": debug}, indent=2))


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
        hough_threshold=args.hough_threshold,
        skip_left_x_lines=args.skip_left_x_lines,
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
                hough_threshold=args.hough_threshold,
                skip_left_x_lines=args.skip_left_x_lines,
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
    preview_path = Path(args.preview)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    debug_preview_path = Path(args.debug_preview)
    debug_preview_path.parent.mkdir(parents=True, exist_ok=True)
    debug_attempt_dir = Path(args.debug_attempt_dir)
    debug_attempt_dir.mkdir(parents=True, exist_ok=True)
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
    roi = parse_roi(args.roi)
    grid_reference = load_grid_reference(args.grid_reference)
    if grid_reference is not None:
        print(f"grid_reference={Path(args.grid_reference).resolve()}")
        if roi is None:
            roi = roi_from_grid_points(
                grid_reference["grid_points"],
                image_width=grid_reference["image_width"],
                image_height=grid_reference["image_height"],
                padding_px=args.wall_padding_px,
            )
            print(f"wall_roi_from_grid_reference={roi}")

    accepted_count = 0
    attempt = 0
    use_timeout_capture = args.capture_timeout_sec > 0
    cap = None if use_timeout_capture else open_camera(args.rtsp_url)
    print(
        f"camera_stream={'timeout_frames' if use_timeout_capture else 'open'} "
        f"preview={preview_path.resolve()} "
        f"debug_preview={debug_preview_path.resolve()} "
        f"debug_attempt_dir={debug_attempt_dir.resolve()}"
    )
    try:
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
            best_attempt: dict[str, object] | None = None
            burst_results: list[dict[str, object]] = []
            cached_grid_found = False
            cached_grid_points = None
            cached_grid_debug: dict[str, object] | None = None
            log_step(f"attempt={attempt} label={format_box_label(region, row, col)} capture_start")
            if args.flush_frames > 0:
                if use_timeout_capture:
                    log_step(f"attempt={attempt} skip_flush_for_timeout_capture=true")
                else:
                    log_step(f"attempt={attempt} flushing_frames={args.flush_frames}")
                    cap = flush_camera_frames(cap, args.flush_frames, reconnect_url=args.rtsp_url)
            for burst_index in range(1, args.burst_frames + 1):
                log_step(f"attempt={attempt} burst={burst_index}/{args.burst_frames} reading_camera")
                try:
                    if use_timeout_capture:
                        burst_capture_path = debug_attempt_dir / f"_attempt_{attempt:04d}_burst_{burst_index:02d}.jpg"
                        capture_one_frame_with_timeout(
                            rtsp_url=args.rtsp_url,
                            output=burst_capture_path,
                            jpeg_quality=args.jpeg_quality,
                            timeout_sec=args.capture_timeout_sec,
                        )
                        image = cv2.imread(str(burst_capture_path))
                        if image is None:
                            raise RuntimeError(f"OpenCV could not read burst frame: {burst_capture_path}")
                        burst_capture_path.unlink(missing_ok=True)
                    else:
                        cap, image = read_camera_frame(cap, reconnect_url=args.rtsp_url)
                except RuntimeError as exc:
                    print(
                        f"camera_timeout=retry_sample attempt={attempt} "
                        f"burst={burst_index}/{args.burst_frames} error={exc}",
                        flush=True,
                    )
                    best_attempt = {
                        "score": -1.0,
                        "image": None,
                        "dot": None,
                        "laser_debug": {"reason": "camera_timeout", "error": str(exc)},
                        "grid_found": bool(grid_reference is not None),
                        "grid_points": None if grid_reference is None else grid_reference["grid_points"],
                        "grid_debug": {} if grid_reference is None else grid_reference["grid_debug"],
                        "box_check": {"box_check": "unknown", "reason": "camera_timeout"},
                        "sample_accepted": False,
                    }
                    break
                log_step(f"attempt={attempt} burst={burst_index}/{args.burst_frames} frame_read")
                dot, laser_debug = detect_laser_dot(
                    image,
                    color=args.laser_color,
                    min_area=args.laser_min_area,
                    max_area=args.laser_max_area,
                    roi=roi,
                    min_saturation=args.laser_min_saturation,
                    min_value=args.laser_min_value,
                    min_green_dominance=args.laser_min_green_dominance,
                )
                log_step(
                    f"attempt={attempt} burst={burst_index}/{args.burst_frames} "
                    f"laser_detected={str(dot is not None).lower()}"
                )
                if grid_reference is not None:
                    grid_found = True
                    grid_points = grid_reference["grid_points"]
                    grid_debug = grid_reference["grid_debug"]
                    cached_grid_found = True
                    cached_grid_points = grid_points
                    cached_grid_debug = grid_debug
                elif cached_grid_debug is None or (not cached_grid_found and burst_index <= args.grid_retry_frames):
                    log_step(f"attempt={attempt} burst={burst_index}/{args.burst_frames} grid_detect_start")
                    grid_found, grid_points, grid_debug = detect_grid_points(
                        image,
                        spec,
                        blue_hue_low=args.blue_hue_low,
                        blue_hue_high=args.blue_hue_high,
                        min_line_length=args.min_line_length,
                        hough_threshold=args.hough_threshold,
                        skip_left_x_lines=args.skip_left_x_lines,
                        roi=roi,
                    )
                    cached_grid_found = grid_found
                    cached_grid_points = grid_points
                    cached_grid_debug = grid_debug
                    log_step(
                        f"attempt={attempt} burst={burst_index}/{args.burst_frames} "
                        f"grid_detect_done grid_found={str(grid_found).lower()}"
                    )
                else:
                    grid_found = cached_grid_found
                    grid_points = cached_grid_points
                    grid_debug = cached_grid_debug
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
                result = {
                    "burst_index": burst_index,
                    "image": image,
                    "dot": dot,
                    "laser_debug": laser_debug,
                    "grid_found": grid_found,
                    "grid_points": grid_points,
                    "grid_debug": grid_debug,
                    "box_check": box_check,
                    "sample_accepted": sample_accepted,
                }
                burst_results.append(
                    {
                        "burst_index": burst_index,
                        "laser_detected": dot is not None,
                        "grid_found": grid_found,
                        "box_check": box_check.get("box_check"),
                        "laser_debug": laser_debug,
                    }
                )
                score = float(laser_debug.get("laser_score", 0.0)) + (1000000.0 if sample_accepted else 0.0)
                if best_attempt is None or score > float(best_attempt["score"]):
                    result["score"] = score
                    best_attempt = result
                if sample_accepted:
                    break
                if args.burst_interval_sec > 0 and burst_index < args.burst_frames:
                    time.sleep(args.burst_interval_sec)

            if best_attempt is None:
                raise RuntimeError("No burst frames were captured.")

            image = best_attempt["image"]
            dot = best_attempt["dot"]
            laser_debug = best_attempt["laser_debug"]
            grid_found = bool(best_attempt["grid_found"])
            grid_points = best_attempt["grid_points"]
            grid_debug = best_attempt["grid_debug"]
            box_check = best_attempt["box_check"]
            sample_accepted = bool(best_attempt["sample_accepted"])
            if image is None:
                print(
                    f"sample_rejected=no_frame box={format_box_label(region, row, col)} "
                    f"accepted_count={accepted_count}/{args.count} "
                    f"reason={box_check.get('reason', 'camera_timeout')}",
                    flush=True,
                )
                continue

            if (
                args.interactive
                and dot is not None
                and grid_found
                and not sample_accepted
                and box_check.get("suggested_box")
            ):
                typed_label = format_box_label(region, row, col)
                suggested_label = str(box_check["suggested_box"])
                print(
                    f"box_mismatch typed={typed_label} suggested={suggested_label} "
                    f"{box_check_summary(box_check)}",
                    flush=True,
                )
                correction = input(
                    "keep this photo? y=typed, s=suggested, new label like 2,3, Enter=reject> "
                ).strip()
                if correction.lower() == "y":
                    box_check = {
                        **box_check,
                        "box_check": "inside",
                        "reason": "user_confirmed_typed_box",
                        "user_override": True,
                    }
                    sample_accepted = True
                elif correction.lower() == "s":
                    region, row, col = parse_box_label(suggested_label)
                    box_check = dot_inside_labeled_box(
                        spec=spec,
                        grid_debug=grid_debug,
                        dot=dot,
                        region=region,
                        row=row,
                        col=col,
                        margin_px=args.box_margin_px,
                    )
                    box_check = {
                        **box_check,
                        "box_check": "inside",
                        "reason": "user_selected_suggested_box",
                        "user_override": True,
                    }
                    sample_accepted = True
                elif correction:
                    region, row, col = parse_box_label(correction)
                    grid_box_center_object_point(spec=spec, row=row, col=col, region=region)
                    box_check = dot_inside_labeled_box(
                        spec=spec,
                        grid_debug=grid_debug,
                        dot=dot,
                        region=region,
                        row=row,
                        col=col,
                        margin_px=args.box_margin_px,
                    )
                    box_check = {
                        **box_check,
                        "box_check": "inside",
                        "reason": "user_corrected_box_label",
                        "user_override": True,
                    }
                    sample_accepted = True

            raw_attempt_path = debug_attempt_dir / f"attempt_{attempt:04d}_raw.jpg"
            debug_attempt_path = debug_attempt_dir / f"attempt_{attempt:04d}_debug.jpg"
            log_step(f"attempt={attempt} writing_images")
            write_image_or_raise(preview_path, image, args.jpeg_quality)
            write_image_or_raise(raw_attempt_path, image, args.jpeg_quality)
            write_image_or_raise(image_path, image, args.jpeg_quality)
            sample = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "image": str(image_path),
                "preview": str(preview_path),
                "debug_preview": str(debug_preview_path),
                "raw_attempt": str(raw_attempt_path),
                "debug_attempt": str(debug_attempt_path),
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
                "grid_reference": None if grid_reference is None else args.grid_reference,
                "laser_debug": laser_debug,
                "grid_debug": grid_debug,
                "box_check": box_check,
                "burst_frames": args.burst_frames,
                "burst_results": burst_results,
                "sample_accepted": sample_accepted,
                "label": args.label,
            }
            save_debug_overlay(
                image=image,
                output=debug_preview_path,
                grid_debug=grid_debug,
                dot=dot,
                box_check=box_check,
                label=format_box_label(region, row, col),
            )
            save_debug_overlay(
                image=image,
                output=debug_attempt_path,
                grid_debug=grid_debug,
                dot=dot,
                box_check=box_check,
                label=format_box_label(region, row, col),
            )
            log_step(f"attempt={attempt} images_written")
            prune_old_files(debug_attempt_dir, "attempt_*_raw.jpg", args.keep_debug_attempts)
            prune_old_files(debug_attempt_dir, "attempt_*_debug.jpg", args.keep_debug_attempts)
            files_written = (
                f"files_written raw={raw_attempt_path.resolve()} "
                f"debug={debug_attempt_path.resolve()} "
                f"latest={preview_path.resolve()}"
            )
            status = "accepted" if sample_accepted else "rejected"
            if sample_accepted or args.save_rejected:
                append_jsonl(samples_path, sample)
                print(
                    f"sample_{status}={image_path} preview={preview_path} debug={debug_preview_path} "
                    f"attempt_debug={debug_attempt_path} "
                    f"box={format_box_label(region, row, col)} "
                    f"accepted_count={accepted_count + int(sample_accepted)}/{args.count} "
                    f"laser_detected={str(dot is not None).lower()} grid_found={str(grid_found).lower()} "
                    f"box_check={box_check.get('box_check')} {box_check_summary(box_check)} "
                    f"{files_written}"
                )
            else:
                image_path.unlink(missing_ok=True)
                print(
                    f"sample_rejected=not_saved preview={preview_path} debug={debug_preview_path} "
                    f"attempt_debug={debug_attempt_path} "
                    f"box={format_box_label(region, row, col)} "
                    f"accepted_count={accepted_count}/{args.count} "
                    f"laser_detected={str(dot is not None).lower()} grid_found={str(grid_found).lower()} "
                    f"box_check={box_check.get('box_check')} {box_check_summary(box_check)} "
                    f"grid_reason={grid_debug.get('reason', 'none')} "
                    f"{files_written}"
                )
            if sample_accepted:
                accepted_count += 1
    finally:
        if cap is not None:
            cap.release()


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
            hough_threshold=args.hough_threshold,
            skip_left_x_lines=args.skip_left_x_lines,
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
    cli_grid_reference = load_grid_reference(args.grid_reference)

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
        sample_reference = load_grid_reference(str(sample["grid_reference"])) if sample.get("grid_reference") else None
        grid_reference = sample_reference or cli_grid_reference
        if grid_reference is not None:
            grid_points = grid_reference["grid_points"]
            grid_debug = grid_reference["grid_debug"]
        else:
            found, grid_points, grid_debug = detect_grid_points(
                image,
                spec,
                blue_hue_low=args.blue_hue_low,
                blue_hue_high=args.blue_hue_high,
                min_line_length=args.min_line_length,
                hough_threshold=args.hough_threshold,
                skip_left_x_lines=args.skip_left_x_lines,
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
        p.add_argument("--min-line-length", type=int, default=25)
        p.add_argument("--hough-threshold", type=int, default=50)
        p.add_argument(
            "--skip-left-x-lines",
            type=int,
            default=0,
            help="Ignore this many detected vertical blue lines from the left before numbering grid columns.",
        )

    def add_laser_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--laser-color", choices=["red", "green"], default="green")
        p.add_argument("--laser-min-area", type=float, default=1.0)
        p.add_argument("--laser-max-area", type=float, default=12000.0)
        p.add_argument("--laser-min-saturation", type=int, default=35)
        p.add_argument("--laser-min-value", type=int, default=120)
        p.add_argument("--laser-min-green-dominance", type=int, default=25)
        p.add_argument(
            "--wall-padding-px",
            type=int,
            default=20,
            help="Padding around grid-reference points when using the grid as the wall-only laser ROI.",
        )

    inspect = sub.add_parser("inspect-grid", help="Check grid detection in one image.")
    inspect.add_argument("--image", default="test_camera.jpg")
    add_grid_args(inspect)
    inspect.set_defaults(func=inspect_grid)

    inspect_laser_cmd = sub.add_parser("inspect-laser", help="Capture/check whether the green laser is visible.")
    inspect_laser_cmd.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    inspect_laser_cmd.add_argument("--image", default=None)
    inspect_laser_cmd.add_argument("--output", default="camera_calibration_runs/latest/laser_test.jpg")
    inspect_laser_cmd.add_argument("--debug-output", default="camera_calibration_runs/latest/laser_test_debug.jpg")
    inspect_laser_cmd.add_argument("--warmup-frames", type=int, default=0)
    inspect_laser_cmd.add_argument("--capture-timeout-sec", type=float, default=6.0)
    inspect_laser_cmd.add_argument("--jpeg-quality", type=int, default=92)
    inspect_laser_cmd.add_argument("--roi", default=None)
    inspect_laser_cmd.add_argument("--grid-reference", default=None)
    add_laser_args(inspect_laser_cmd)
    inspect_laser_cmd.set_defaults(func=inspect_laser)

    capture = sub.add_parser("capture-grid", help="Capture many dog-camera grid images.")
    capture.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    capture.add_argument("--output-dir", default="camera_calibration_runs/latest/images")
    capture.add_argument("--count", type=int, default=200)
    capture.add_argument("--interval-sec", type=float, default=0.1)
    capture.add_argument("--jpeg-quality", type=int, default=92)
    capture.add_argument("--progress-every", type=int, default=25)
    add_grid_args(capture)
    capture.set_defaults(func=capture_grid)

    reference = sub.add_parser("capture-grid-reference", help="Capture one lights-on grid reference for dark laser samples.")
    reference.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    reference.add_argument("--output", default="camera_calibration_runs/latest/grid_reference.json")
    reference.add_argument("--image", default=None, help="Use an existing grid image instead of capturing from RTSP.")
    reference.add_argument("--image-output", default="camera_calibration_runs/latest/grid_reference.jpg")
    reference.add_argument("--capture-timeout-sec", type=float, default=6.0)
    reference.add_argument("--jpeg-quality", type=int, default=92)
    add_grid_args(reference)
    reference.set_defaults(func=capture_grid_reference)

    laser = sub.add_parser("capture-laser-samples", help="Capture laser samples labeled by grid box.")
    laser.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    laser.add_argument("--output-dir", default="camera_calibration_runs/latest/laser_images")
    laser.add_argument("--samples", default="camera_calibration_runs/latest/laser_samples.jsonl")
    laser.add_argument("--grid-reference", default=None)
    laser.add_argument("--count", type=int, default=50)
    laser.add_argument("--interactive", action="store_true")
    laser.add_argument("--box-region", choices=["lower", "top_extension"], default="lower")
    laser.add_argument("--box-row", type=int, default=None)
    laser.add_argument("--box-col", type=int, default=None)
    laser.add_argument("--label", default="")
    laser.add_argument("--save-rejected", action="store_true")
    laser.add_argument("--box-margin-px", type=float, default=12.0)
    laser.add_argument("--preview", default="camera_calibration_runs/latest/latest_laser_attempt.jpg")
    laser.add_argument("--debug-preview", default="camera_calibration_runs/latest/latest_laser_debug.jpg")
    laser.add_argument("--debug-attempt-dir", default="camera_calibration_runs/latest/debug_attempts")
    laser.add_argument("--keep-debug-attempts", type=int, default=20)
    laser.add_argument("--burst-frames", type=int, default=5)
    laser.add_argument("--burst-interval-sec", type=float, default=0.08)
    laser.add_argument("--grid-retry-frames", type=int, default=2)
    laser.add_argument(
        "--flush-frames",
        type=int,
        default=12,
        help="Discard this many buffered frames after each label before detecting the laser.",
    )
    laser.add_argument(
        "--capture-timeout-sec",
        type=float,
        default=6.0,
        help="Timeout for each RTSP frame during laser capture. Use 0 for the old persistent camera mode.",
    )
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
    laser_calibrate.add_argument("--grid-reference", default=None)
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
