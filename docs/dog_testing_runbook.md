# Aegis/D1 Dog Testing And Calibration Runbook

This is the command guide for the height-measurement project.

There are two separate work paths:

1. **Calibration path, today**: use the wall grid and laser samples to calibrate the dog camera for accuracy.
2. **Tilt path, later**: use dog tilt telemetry and camera geometry for a Measure-app style workflow.

Today, do the **calibration path** first.

## 0. Project Files

Use these files:

```text
docs/dog_testing_runbook.md              # this guide
docs/camera_height_workflow.md           # height workflow overview
examples/vision/grid_laser_calibration.py # grid + laser calibration commands
examples/vision/height_calculator.py     # YOLO/distance/height helper commands
examples/vision/tilt_telemetry_probe.py  # tilt telemetry test, for later
models/yolov8n.onnx                      # YOLO model
requirements-vision.txt                  # Python vision dependencies
test_camera.jpg                          # sample grid image
```

## 1. Connect To The Dog

Known login for this dog:

```bash
ssh firefly@192.168.234.1
```

When prompted, enter the robot password provided with the dog.

If the dog has WiFi working, connect your laptop to the dog network and use the
command above.

If the dog does not have WiFi, use Ethernet:

1. Plug Ethernet into the dog and your router/switch, or directly into your laptop.
2. Find the dog IP address.
3. SSH into the dog.

If you have monitor/keyboard access to the dog, run this on the dog:

```bash
ip addr
```

Look for an address like:

```text
192.168.x.x
10.x.x.x
172.16.x.x
```

If the IP is still `192.168.234.1`, SSH from your laptop:

```bash
ssh firefly@192.168.234.1
```

If Ethernet gives the dog a different IP, keep the same username and replace the
IP:

```bash
ssh firefly@<robot-ip>
```

If SSH is not available, run the same commands directly on the dog with
monitor/keyboard.

## 2. Clean The Dog And Copy Fresh Files

Run these commands from your Mac terminal, not inside the SSH session:

```bash
ssh firefly@192.168.234.1 'rm -rf ~/Aegies-Height && mkdir -p ~/Aegies-Height'

cd "/Users/agentech/Documents/Faraday Future Robot SDK"

rsync -av --delete \
  --exclude '.git/' \
  --exclude 'camera_calibration_runs/' \
  ./ firefly@192.168.234.1:~/Aegies-Height/
```

This leaves the dog clean. It copies the code, `test_camera.jpg`, and the
annotated reference image at:

```text
~/Aegies-Height/docs/images/l_shape_grid_box_labels.jpg
```

Then SSH in:

```bash
ssh firefly@192.168.234.1
cd ~/Aegies-Height
```

Check that the only calibration starting image/reference files are present:

```bash
ls test_camera.jpg docs/images/l_shape_grid_box_labels.jpg
ls camera_calibration_runs
```

The last command should say `No such file or directory` before you start a new
calibration. That is good: it means no old calibration run is left.

If the dog has GitHub access later, this also works:

```bash
git clone git@github.com:wesleyfan2015/Aegies-Height.git
cd Aegies-Height
```

If Ethernet gives the dog a different IP, replace `192.168.234.1` with that IP.

To delete only old calibration images later, run this on the dog:

```bash
rm -rf ~/Aegies-Height/camera_calibration_runs
```

## 3. Install Dependencies

On the dog:

```bash
python3 -m pip install -r requirements-vision.txt
```

If you need the SDK wheel on the robot:

```bash
python3 -m pip install wheels/ff_sdk-*-linux_aarch64.whl
```

If testing from a Linux laptop connected to the dog:

```bash
python3 -m pip install wheels/ff_sdk-*-linux_x86_64.whl
```

## 4. Quick Software Checks

Check YOLO:

```bash
python3 examples/vision/height_calculator.py verify-yolo
```

Expected:

```text
yolo_loaded=true
```

Check calibration script commands:

```bash
python3 examples/vision/grid_laser_calibration.py --help
```

Expected commands:

```text
inspect-grid
capture-grid
capture-laser-samples
calibrate
calibrate-laser
```

## 5. Today: Wall Grid + Laser Calibration

This is the path to do today.

The grid and laser calibration does **not** train AI. It solves camera geometry:

```text
real grid position in centimeters <-> camera pixel position
```

The output is:

```text
camera_calibration_runs/latest/calibration.json
```

That file contains camera intrinsics and distortion coefficients. Later, the
tilt/height math can use this to be more accurate.

### 5.1 Confirm Grid Measurements

Before running commands, confirm:

```text
grid_rows       = number of horizontal grid lines/intersections
grid_cols       = number of vertical grid lines/intersections
square_size_cm  = real measured square size
```

Current default:

```text
grid_shape = l_shape
lower grid intersections = 8 rows x 8 cols
lower grid boxes = 7 rows x 7 cols
top extension boxes = 4 rows x 2 cols
square_size_cm = 15
roi = 700,420,320,390 for test_camera.jpg
```

Use `docs/images/l_shape_grid_box_labels.jpg` as the reference image. Lower
boxes are labeled `1,1` through `7,7`. Top-extension boxes are labeled `T1,1`
through `T4,2`.

### 5.2 Inspect The Existing Test Image

Run:

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

Why `76`:

```text
64 lower-grid intersections + 12 top-extension intersections = 76
```

If `grid_found=false`, check:

- the grid line color is visible
- the image is not too dark
- the grid row/column counts are correct
- `--blue-hue-low` / `--blue-hue-high` may need adjustment
- `--min-line-length` may need adjustment

### 5.3 Capture Grid Images From The Dog Camera

Run this while the dog camera sees the wall grid:

```bash
python3 examples/vision/grid_laser_calibration.py capture-grid \
  --count 200 \
  --interval-sec 0.1 \
  --roi 700,420,320,390 \
  --min-line-length 25
```

Move the dog/camera or grid view enough that the grid appears in different parts
of the image. Variety matters more than thousands of identical images.

Saved images:

```text
camera_calibration_runs/latest/images/
```

Capture report:

```text
camera_calibration_runs/latest/images/capture_records.json
```

Good result:

```text
accepted_count should be at least 30
```

### 5.4 Calibrate From Grid Images

Run:

```bash
python3 examples/vision/grid_laser_calibration.py calibrate \
  --image-dir camera_calibration_runs/latest/images \
  --output camera_calibration_runs/latest/calibration.json \
  --min-accepted 30 \
  --roi 700,420,320,390 \
  --min-line-length 25
```

Good result:

```text
calibration_saved=camera_calibration_runs/latest/calibration.json
accepted_count >= 30
rms_reprojection_error is low
```

Lower RMS is better. If RMS is high, capture better grid images.

### 5.5 Capture Laser-Labeled Samples

This is the assisted calibration step.

Point the laser into a grid box, then tell the script which box it is in.
OpenCV detects the laser dot and checks whether it is inside the box label you
typed.

Lower boxes are counted from the top-left starting at:

```text
row 1, col 1
```

The lower rectangle has:

```text
7 box rows
7 box columns
```

The top extension uses labels `Trow,col`:

```text
T1,1 is the upper-left top-extension box
T4,2 is the lower-right top-extension box
```

Run:

```bash
python3 examples/vision/grid_laser_calibration.py capture-laser-samples \
  --interactive \
  --count 50 \
  --roi 700,420,320,390 \
  --min-line-length 25
```

If the live camera says `laser_detected=true grid_found=false`, the dog may
still see the grid. It usually means the ROI crop is wrong for the live camera
position. Stop the command, clean the bad partial run, and use a larger ROI:

```bash
rm -rf camera_calibration_runs

python3 examples/vision/grid_laser_calibration.py capture-laser-samples \
  --interactive \
  --count 50 \
  --roi 650,380,450,460 \
  --min-line-length 25
```

If that still cannot find the grid, test without an ROI:

```bash
rm -rf camera_calibration_runs

python3 examples/vision/grid_laser_calibration.py capture-laser-samples \
  --interactive \
  --count 50 \
  --min-line-length 25
```

The script only saves accepted samples by default. Rejected attempts are not
added to `laser_samples.jsonl`.

When prompted:

```text
sample 1/50 box row,col (or q)>
```

Type values like:

```text
3,5
T1,1
```

Saved laser images:

```text
camera_calibration_runs/latest/laser_images/
```

Saved labels:

```text
camera_calibration_runs/latest/laser_samples.jsonl
```

Good result for each sample:

```text
laser_detected=true
grid_found=true
box_check=inside
```

If `box_check=outside`, the laser dot is not inside the box you typed. Move the
laser or type the correct label on the next sample. The final calibration
rejects outside samples.

If the laser is not detected:

- try a darker room
- use the green laser; green is now the default in the scripts
- adjust `--laser-min-area`
- lower `--laser-min-saturation`
- lower `--laser-min-value`
- adjust `--laser-max-area`

### 5.6 Calibrate With Laser Samples

Run:

```bash
python3 examples/vision/grid_laser_calibration.py calibrate-laser \
  --samples camera_calibration_runs/latest/laser_samples.jsonl \
  --output camera_calibration_runs/latest/calibration.json \
  --min-accepted 10 \
  --roi 700,420,320,390 \
  --min-line-length 25
```

Good result:

```text
calibration_saved=camera_calibration_runs/latest/calibration.json
accepted_count >= 10
rms_reprojection_error is low
laser_error_px_avg is low
```

Keep the generated file:

```text
camera_calibration_runs/latest/calibration.json
```

## 6. How To Check If Calibration Is Correct

Check the calibration JSON:

```bash
python3 -m json.tool camera_calibration_runs/latest/calibration.json | head -80
```

Look for:

```text
camera_matrix
distortion_coefficients
rms_reprojection_error
accepted_count
rejected_count
```

Good signs:

- `accepted_count` is high
- `rejected_count` is low
- `rms_reprojection_error` is low
- laser samples show low average pixel error
- accepted images are from varied positions/angles

Bad signs:

- most images are rejected
- the wrong grid row/column count was used
- the grid was not flat
- square size was measured wrong
- the laser dot was labeled with the wrong box number
- the laser reflected or bloomed too much

## 7. Tilt Path For Later

Do this later, after or separate from calibration.

Tilt testing answers:

- does Python expose `attitude()`?
- does Python expose `rpy()`?
- does pitch telemetry change after a small pitch command?
- does the camera image change when the dog tilts?

Safe no-tilt probe:

```bash
python3 examples/vision/tilt_telemetry_probe.py \
  --host 192.168.234.1 \
  --stand \
  --skip-tilt
```

Tiny tilt probe:

```bash
python3 examples/vision/tilt_telemetry_probe.py \
  --host 192.168.234.1 \
  --stand \
  --pitch-vel 0.04 \
  --pitch-seconds 0.5
```

Opposite direction:

```bash
python3 examples/vision/tilt_telemetry_probe.py \
  --host 192.168.234.1 \
  --stand \
  --pitch-vel -0.04 \
  --pitch-seconds 0.5
```

Look for:

```text
connected: true
available_methods includes "attitude"
available_methods includes "rpy"
initial_rpy
after_tilt_rpy
before_image.detections
after_image.detections
```

Saved images:

```text
tilt_probe_runs/latest/
```

## 8. Questions For The Dog Developers

Ask:

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

## 9. What To Send Back After Today

Send:

- terminal output from `inspect-grid`
- terminal output from `capture-grid`
- terminal output from `calibrate`
- terminal output from `capture-laser-samples`
- terminal output from `calibrate-laser`
- `camera_calibration_runs/latest/calibration.json`
- a few accepted grid images
- a few laser images
- notes on grid square size and grid row/column count

That gives enough information to verify whether the camera calibration is good.
