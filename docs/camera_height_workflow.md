# Robot Camera Height Workflow

This is the new direction of the project:

- Code runs on the Aegis/D1 robot.
- The robot code talks directly to the dog camera.
- The dog camera is calibrated from the wall grid shown in `test_camera.jpg`.
- Person height is estimated with YOLO/OpenCV plus radar distance.

## Setup

Install the vision dependencies on the robot/Linux environment:

```bash
python3 -m pip install -r requirements-vision.txt
```

The included model is:

```text
models/yolov8n.onnx
```

This is YOLO running through OpenCV DNN, so no heavyweight `ultralytics` or
PyTorch install is required for the robot path.

## 1. Check The Grid

Measure the real grid square size first. Then run:

```bash
python3 examples/vision/height_calculator.py inspect-grid \
  --image test_camera.jpg \
  --grid-rows 8 \
  --grid-cols 8 \
  --square-size-cm 10
```

Adjust `--grid-rows`, `--grid-cols`, and `--square-size-cm` to match the actual tape grid.

Verify YOLO loads:

```bash
python3 examples/vision/height_calculator.py verify-yolo
```

## 2. Capture Many Calibration Images

Run this on the robot while the grid is visible from many angles and distances:

```bash
python3 examples/vision/height_calculator.py capture-grid \
  --count 1000 \
  --interval-sec 0.05 \
  --grid-rows 8 \
  --grid-cols 8 \
  --square-size-cm 10
```

Move the robot/camera so the grid appears in different parts of the image. A thousand nearly identical pictures are less useful than fewer pictures with real angle and position variety.

## 3. Capture Laser-Labeled Grid Samples

This is the assisted calibration mode you described: a person points a laser into one grid box, and you tell the script which box it is in.

Boxes are numbered from the top-left, starting at `1,1`. If the detected grid has `8` vertical intersections and `8` horizontal intersections, it has `7 x 7` boxes.

Interactive mode:

```bash
python3 examples/vision/height_calculator.py capture-laser-samples \
  --interactive \
  --count 100 \
  --grid-rows 8 \
  --grid-cols 8 \
  --square-size-cm 10
```

For every capture, type the box as:

```text
row,col
```

Example:

```text
3,5
```

Single known box mode:

```bash
python3 examples/vision/height_calculator.py capture-laser-samples \
  --count 10 \
  --box-row 3 \
  --box-col 5 \
  --grid-rows 8 \
  --grid-cols 8 \
  --square-size-cm 10
```

The script saves each image and appends one row per image to:

```text
camera_calibration_runs/latest/laser_samples.jsonl
```

## 4. Calibrate

```bash
python3 examples/vision/height_calculator.py calibrate \
  --image-dir camera_calibration_runs/latest/images \
  --output camera_calibration_runs/latest/calibration.json \
  --min-accepted 30 \
  --grid-rows 8 \
  --grid-cols 8 \
  --square-size-cm 10
```

The calibration file contains the camera matrix, distortion coefficients, and reprojection error.

To use the laser-labeled dataset:

```bash
python3 examples/vision/height_calculator.py calibrate-laser \
  --samples camera_calibration_runs/latest/laser_samples.jsonl \
  --output camera_calibration_runs/latest/calibration.json \
  --min-accepted 10 \
  --grid-rows 8 \
  --grid-cols 8 \
  --square-size-cm 10
```

This uses the detected grid intersections plus the laser-labeled box center.
The report includes average/max laser reprojection error in pixels.

## 5. Estimate Person Height

Radar supplies the distance in centimeters. YOLO supplies the person box.

```bash
python3 examples/vision/height_calculator.py estimate-height \
  --image person.jpg \
  --distance-cm 250 \
  --calibration camera_calibration_runs/latest/calibration.json
```

For the live robot path, capture one dog-camera frame and estimate height in one command:

```bash
python3 examples/vision/height_calculator.py capture-height \
  --distance-cm 250 \
  --calibration camera_calibration_runs/latest/calibration.json
```

If the person is too close, too far, cut off, or off-center, use auto framing:

```bash
python3 examples/vision/height_calculator.py auto-capture-height \
  --distance-cm 250 \
  --calibration camera_calibration_runs/latest/calibration.json
```

By default this only prints the movement it would make. To actually move the dog,
use:

```bash
python3 examples/vision/height_calculator.py auto-capture-height \
  --distance-cm 250 \
  --calibration camera_calibration_runs/latest/calibration.json \
  --execute-motion
```

Auto framing uses the raw `zsibot` backend, not `sess.motion.cmd_vel`.

If YOLO is not ready, test the math with a manual box:

```bash
python3 examples/vision/height_calculator.py estimate-height \
  --image person.jpg \
  --distance-cm 250 \
  --manual-box 720,120,260,820 \
  --calibration camera_calibration_runs/latest/calibration.json
```

## Accuracy Notes

Radar distance must be the distance from the camera plane to the person, not to a wall behind them.

The person must be full body in frame. If the head or feet are cut off, the height estimate is not valid.

The tape grid must be flat and measured accurately. If the grid detector cannot find stable intersections, use a printed checkerboard or ChArUco board instead.
