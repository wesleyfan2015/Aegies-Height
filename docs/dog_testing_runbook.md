# Aegis/D1 Dog Testing Runbook

This document is the practical checklist for testing the dog before the final
camera-only height measurement workflow is finished.

The goal today is to answer these questions:

- Can we connect to the dog over Ethernet or local terminal?
- Can the dog camera stream be opened?
- Can YOLO detect a full person?
- Does the Python backend expose `rpy()` pitch telemetry?
- Does the Python backend expose `attitude()` pitch/tilt control?
- Does a small pitch command change the reported pitch?

If the answer is yes, we can build the Measure-app style workflow where the dog
tilts itself and OpenCV/YOLO calculates height from camera geometry.

## 0. Files You Will Use

Main probe script:

```bash
examples/vision/tilt_telemetry_probe.py
```

Vision helper script:

```bash
examples/vision/height_calculator.py
```

Vision/runtime dependencies:

```bash
requirements-vision.txt
```

YOLO model:

```bash
models/yolov8n.onnx
```

## 1. Connect To The Dog

If the dog has WiFi working, connect your laptop to the dog network.

If the dog does not have WiFi, use Ethernet:

1. Plug Ethernet into the dog and your router/switch, or directly into your laptop.
2. Find the dog IP address.
3. SSH into the dog.

If you have access to the dog directly with monitor/keyboard, run this on the dog:

```bash
ip addr
```

Look for an address like:

```text
192.168.x.x
10.x.x.x
172.16.x.x
```

Then SSH from your laptop:

```bash
ssh <user>@<robot-ip>
```

Example:

```bash
ssh pi@192.168.1.42
```

If SSH is not available, use a monitor/keyboard on the dog and run the same
commands locally.

## 2. Get The Project Onto The Dog

If the repo is already on the dog, go to it:

```bash
cd /path/to/Aegies-Height
```

If it is not on the dog and the dog has internet/GitHub access:

```bash
git clone git@github.com:wesleyfan2015/Aegies-Height.git
cd Aegies-Height
```

If the dog does not have GitHub access, copy the folder from your laptop:

```bash
scp -r "/Users/agentech/Documents/Faraday Future Robot SDK" <user>@<robot-ip>:~/Aegies-Height
```

Then SSH in and enter the copied folder:

```bash
ssh <user>@<robot-ip>
cd ~/Aegies-Height
```

## 3. Install Dependencies

On the dog:

```bash
python3 -m pip install -r requirements-vision.txt
```

If you use the SDK wheel on the robot, install the correct wheel too:

```bash
python3 -m pip install wheels/ff_sdk-*-linux_aarch64.whl
```

If you are testing from a Linux laptop connected to the dog:

```bash
python3 -m pip install wheels/ff_sdk-*-linux_x86_64.whl
```

## 4. Verify YOLO

Run:

```bash
python3 examples/vision/height_calculator.py verify-yolo
```

Expected result:

```text
yolo_loaded=true
```

If this fails, fix dependencies/model path before testing tilt.

## 5. Run The Safe Probe First

This connects to the dog, reads telemetry if available, captures a camera image,
and runs YOLO. It does **not** send a tilt command.

Default dog host:

```bash
python3 examples/vision/tilt_telemetry_probe.py \
  --host 192.168.234.1 \
  --stand \
  --skip-tilt
```

If the dog is on Ethernet with another IP:

```bash
python3 examples/vision/tilt_telemetry_probe.py \
  --host <robot-ip> \
  --stand \
  --skip-tilt
```

Example:

```bash
python3 examples/vision/tilt_telemetry_probe.py \
  --host 192.168.1.42 \
  --stand \
  --skip-tilt
```

Look for these fields in the output:

```text
connected: true
available_methods
initial_rpy
before_image
detections
```

The script saves images here:

```bash
tilt_probe_runs/latest/
```

## 6. Run A Tiny Tilt Probe

Only run this after the safe probe connects successfully.

Make sure the dog is in a clear area and someone is ready to stop it.

Use a very small pitch velocity first:

```bash
python3 examples/vision/tilt_telemetry_probe.py \
  --host <robot-ip> \
  --stand \
  --pitch-vel 0.04 \
  --pitch-seconds 0.5
```

If you want to test the opposite direction:

```bash
python3 examples/vision/tilt_telemetry_probe.py \
  --host <robot-ip> \
  --stand \
  --pitch-vel -0.04 \
  --pitch-seconds 0.5
```

Do not increase these values until we understand the units and direction.

## 7. What The Output Means

Important fields:

```text
available_methods
initial_rpy
after_tilt_rpy
before_image.detections
after_image.detections
tilt_command
```

What we want:

```text
available_methods includes "attitude"
available_methods includes "rpy"
initial_rpy has 3 values
after_tilt_rpy changes after the pitch command
YOLO detects a person box
```

If `attitude` is missing, the Python backend may not expose body pitch control.

If `rpy` is missing, we need a different telemetry API.

If `rpy` does not change after the tilt command, the command may use different
units, the command may be ignored, or pitch telemetry may be reported somewhere
else.

## 8. Camera-Only Height Logic We Are Trying To Enable

The final camera-only measurement should work like this:

1. Dog stands.
2. Dog tilts/aims camera.
3. OpenCV/YOLO finds the person box.
4. We read actual pitch telemetry at capture time.
5. We combine:

```text
camera height from floor
camera pitch
camera mounting offset
vertical camera FOV
person head/feet pixel positions
```

Then we calculate height from geometry.

## 9. Questions For The Dog Developers

Send them these questions:

```text
1. What are the units for attitude(roll_vel, pitch_vel, yaw_vel, height_vel)?
2. Does positive pitch_vel tilt the camera/body upward or downward?
3. What are the safe pitch limits while standing?
4. Does rpy()[1] or pose.pitch report radians or degrees?
5. Is pose.pitch body pitch, IMU pitch, or camera pitch?
6. What is the camera mounting angle offset relative to the body frame?
7. What is the camera center height from the floor in normal standing pose?
8. Is the dog camera fixed to the body, or does it have its own tilt actuator?
9. Is there an API to read current camera pitch directly?
```

## 10. What To Send Back After Testing

Send:

- full terminal output from the safe probe
- full terminal output from the tiny tilt probe
- the saved images from `tilt_probe_runs/latest/`
- the dog IP/network setup you used
- whether the person was visible in the camera frame

That is enough to decide whether we can build the camera-only tilt measurement
without waiting for the distance sensor.
