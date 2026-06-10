# Robot Camera Height Workflow

The project now has two paths that work together:

1. **Camera calibration**: wall grid + laser samples solve camera intrinsics and distortion.
2. **Height measurement**: YOLO/OpenCV detects the person, and later dog tilt telemetry helps calculate height.

For today, do the **camera calibration** path.

## Files

```text
examples/vision/grid_laser_calibration.py # today's calibration script
examples/vision/height_calculator.py      # height/distance helper script
examples/vision/tilt_telemetry_probe.py   # later tilt telemetry probe
docs/dog_testing_runbook.md               # full command runbook
test_camera.jpg                           # sample grid image
models/yolov8n.onnx                       # YOLO model
```

## Today: Calibrate The Camera

The wall grid + laser process does not train a model. It solves geometry:

```text
known real-world grid/laser point -> camera pixel point
```

The output is:

```text
camera_calibration_runs/latest/calibration.json
```

That file is what we use later to improve height accuracy.

### 1. Inspect The Grid

```bash
python3 examples/vision/grid_laser_calibration.py inspect-grid \
  --image test_camera.jpg \
  --grid-rows 12 \
  --grid-cols 7 \
  --square-size-cm 10
```

Good result:

```text
grid_found=true
point_count=84
```

### 2. Capture Grid Images

```bash
python3 examples/vision/grid_laser_calibration.py capture-grid \
  --count 200 \
  --interval-sec 0.1 \
  --grid-rows 12 \
  --grid-cols 7 \
  --square-size-cm 10
```

Saved to:

```text
camera_calibration_runs/latest/images/
```

### 3. Calibrate From Grid Images

```bash
python3 examples/vision/grid_laser_calibration.py calibrate \
  --image-dir camera_calibration_runs/latest/images \
  --output camera_calibration_runs/latest/calibration.json \
  --min-accepted 30 \
  --grid-rows 12 \
  --grid-cols 7 \
  --square-size-cm 10
```

### 4. Capture Laser-Labeled Samples

Point the laser into a grid box and enter the box as `row,col`.

```bash
python3 examples/vision/grid_laser_calibration.py capture-laser-samples \
  --interactive \
  --count 50 \
  --grid-rows 12 \
  --grid-cols 7 \
  --square-size-cm 10
```

Example prompt answer:

```text
3,5
```

Saved to:

```text
camera_calibration_runs/latest/laser_images/
camera_calibration_runs/latest/laser_samples.jsonl
```

### 5. Calibrate With Laser Samples

```bash
python3 examples/vision/grid_laser_calibration.py calibrate-laser \
  --samples camera_calibration_runs/latest/laser_samples.jsonl \
  --output camera_calibration_runs/latest/calibration.json \
  --min-accepted 10 \
  --grid-rows 12 \
  --grid-cols 7 \
  --square-size-cm 10
```

Good output includes:

```text
accepted_count >= 10
rms_reprojection_error
laser_error_px_avg
```

## Later: Dog Tilt Measurement

Later, we keep the tilt path. The dog should tilt/aim the camera, YOLO finds the
person, and we use pitch telemetry plus calibrated camera geometry.

Probe command:

```bash
python3 examples/vision/tilt_telemetry_probe.py \
  --host 192.168.234.1 \
  --stand \
  --skip-tilt
```

Tiny tilt command:

```bash
python3 examples/vision/tilt_telemetry_probe.py \
  --host 192.168.234.1 \
  --stand \
  --pitch-vel 0.04 \
  --pitch-seconds 0.5
```

## Full Runbook

Use this for exact setup, SSH, calibration, checking, and tilt commands:

```text
docs/dog_testing_runbook.md
```
