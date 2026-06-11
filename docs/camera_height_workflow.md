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
  --roi 700,420,320,390 \
  --min-line-length 25
```

Good result:

```text
grid_found=true
point_count=76
```

The current target is an L-shaped grid: lower `7x7` boxes plus the top `4x2`
box extension. Lower labels are `row,col` such as `1,1`. Top-extension labels
are `Trow,col` such as `T1,1`.

### 2. Capture Grid Images

```bash
python3 examples/vision/grid_laser_calibration.py capture-grid \
  --count 200 \
  --interval-sec 0.1 \
  --roi 700,420,320,390 \
  --min-line-length 25
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
  --roi 700,420,320,390 \
  --min-line-length 25
```

### 4. Capture Laser-Labeled Samples

Point the laser into a grid box and enter the box label. Use `row,col` for the
lower rectangle and `Trow,col` for the top extension. OpenCV checks whether the
detected laser dot is inside the label you typed.

```bash
python3 examples/vision/grid_laser_calibration.py capture-laser-samples \
  --interactive \
  --count 50 \
  --roi 700,420,320,390 \
  --min-line-length 25
```

If the live stream prints `grid_found=false`, the grid is probably outside the
ROI crop. Clean the partial run and use a larger ROI:

```bash
rm -rf camera_calibration_runs

python3 examples/vision/grid_laser_calibration.py capture-laser-samples \
  --interactive \
  --count 50 \
  --roi 650,380,450,460 \
  --min-line-length 25
```

Example prompt answer:

```text
3,5
T1,1
```

Saved to:

```text
camera_calibration_runs/latest/laser_images/
camera_calibration_runs/latest/laser_samples.jsonl
```

Good sample output includes:

```text
laser_detected=true grid_found=true box_check=inside
```

If `box_check=outside`, that sample is rejected during final calibration.

### 5. Calibrate With Laser Samples

```bash
python3 examples/vision/grid_laser_calibration.py calibrate-laser \
  --samples camera_calibration_runs/latest/laser_samples.jsonl \
  --output camera_calibration_runs/latest/calibration.json \
  --min-accepted 10 \
  --roi 700,420,320,390 \
  --min-line-length 25
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
