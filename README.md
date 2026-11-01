# AI Surveillance Rover – Suspicious Activity Detection

Real-time detection of **fighting, stealing, loitering and unauthorised access** from a webcam, video file or images. Runs on an ordinary laptop CPU, no GPU needed. Fighting is detected by a small neural network trained on RWF-2000 (included); the training pipeline is in [`docs/TRAINING.md`](docs/TRAINING.md).

This is an AI-powered surveillance rover (final-year project). The rover (Arduino + Raspberry Pi) streams its camera over Wi-Fi to a laptop, which runs the detection, drives a stop-and-scan patrol, and sends alerts to a FastAPI backend with a phone-friendly dashboard.

**How everything works, how the rover behaves, and how to demonstrate it with videos (no real people needed): [`docs/HOW_IT_WORKS_AND_DEMO.md`](docs/HOW_IT_WORKS_AND_DEMO.md).** Phone app plan: [`docs/MOBILE_APP_PLAN.md`](docs/MOBILE_APP_PLAN.md).

**Building the full rover: follow [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md)**, one guide from wiring and environments to testing the physical rover step by step. Training the AI: [`docs/TRAINING.md`](docs/TRAINING.md). Testing from 0 to 100: [`docs/TESTING.md`](docs/TESTING.md). Project plan: [`docs/ROADMAP.md`](docs/ROADMAP.md).

## What it detects

| Activity | How | Training needed |
|---|---|---|
| People, body pose, ID tracking | YOLO11n-pose + ByteTrack (pretrained on COCO) | No |
| Loitering | Same person in view (or inside a loiter zone) longer than N seconds | No |
| Unauthorised access | A person enters a restricted zone you draw on screen | No |
| Fighting | A GRU neural network reads the skeletons of two nearby people over 2 s; alert when its last few judgements are above the threshold (rule chosen by cross-validation). The old hand-tuned rule is kept as a baseline (`fight.method: rules`) | **Yes, trained on RWF-2000** (`models/fight_gru.pt`, included) |
| Stealing | An object (bag, laptop, phone, bottle…) sits still and becomes "guarded"; a hand touches it and it is moved away or disappears | No (pretrained YOLO11n object model) |

```
camera / video ──► YOLO11n-pose + ByteTrack ──► per-person keypoint history ──┬─► loitering rule
                   YOLO11n objects (every 3rd frame) ──► object states ───────┤─► restricted-zone rule
                                                                              ├─► fight model (trained GRU)
                                                                              └─► theft detector
                                                                                     │
                     on-screen banner + sound + snapshot + 4 s clip + alerts.jsonl ◄─┘ (+ optional POST to backend)
```

## Setup

Requires **Python 3.10** (3.9–3.11 also work).

```bash
git clone https://github.com/mahum24/ai-surveillance-rover.git
cd ai-surveillance-rover

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python tools/get_test_videos.py    # downloads the two test videos (~17 MB)
```

The YOLO model weights (`models/yolo11n-pose.pt`, `models/yolo11n.pt`, ~11 MB) download automatically the first time you run the app.

## Testing (no camera needed)

Run these one at a time. A window opens for each; videos close by themselves at the end, or press **Q** to stop early.

### 1. People and skeletons (images)

```bash
python run.py --source tests/images
```

**Expected:** a football photo with 2 people in green boxes with skeletons. Press any key for the next image (bus, 3 people), then any key to finish. Annotated copies are saved to `outputs/`.

### 2. Loitering

```bash
python run.py --source tests/videos/walking.avi --set loitering.seconds=8
```

**Expected:** people walking with an ID and a seconds counter. About 30 s in, one person who stays around turns **orange** and a red banner shows **`LOITERING: ID 45 in view for 8s`**.

### 3. Unauthorised access

```bash
python run.py --source tests/videos/walking.avi --set zones.file=tests/zone_road.json --set zones.anchor=feet --set loitering.enabled=false
```

**Expected:** a **red zone** over the right side of the road. Anyone whose feet enter it turns red and triggers **`UNAUTHORISED ACCESS: ID n entered restricted zone`** (around 30 alerts, as many people cross it).

### 4. Fighting

```bash
python run.py --source tests/videos/fight.avi --set loitering.enabled=false --debug
```

**Expected:** the terminal prints `Fight detection: trained model fight_gru.pt`, and the two people fighting in the right corner turn **red** with **`FIGHT`** (the alert shows the model's probability), while bystanders stay green. `--debug` also shows each person's arm-motion value `m=` used by the rule baseline. To compare with the old rule: add `--set fight.method=rules`.

### 5. Stealing (logic test)

```bash
python tests/test_theft_sim.py
```

**Expected:**

```
[PASS] laptop picked up     -> ['t=5.6s STEALING: ID 1 picked up the laptop']
[PASS] laptop vanished      -> ['t=6.4s STEALING: ID 1 took away the laptop']
[PASS] laptop never touched -> no alert
```

This simulates a laptop and a hand to check the stealing logic. For a real test, record a short phone video: put a bottle or phone on a table, wait 3 s, then pick it up and walk away, and run `python run.py --source your_video.mp4`.

### Where the results go

Every alert saves a snapshot `.jpg`, the 4 s of footage before it as `.mp4`, and a JSON line in `alerts/alerts.jsonl`:

```json
{"time": "2026-10-31T07:42:42", "type": "fight", "message": "FIGHT: ID 9 and ID 15", "track_ids": [9, 15],
 "snapshot": "20261031/20261031_074242_fight-9-15.jpg", "clip": "20261031/20261031_074242_fight-9-15.mp4"}
```

## Running with a webcam

```bash
python run.py
```

**macOS:** the first time, allow camera access for your terminal app (Terminal / iTerm / VS Code) in **System Settings → Privacy & Security → Camera**, then quit and reopen the terminal.

### Keys

| Key | Action |
|---|---|
| Z | draw a **restricted** zone: click the corners, ENTER to save, ESC to cancel |
| L | draw a **loiter** zone (without one, loitering counts time anywhere in view) |
| C | clear all zones |
| V | show/hide debug values |
| P | pause |
| Q / ESC | quit |

Zones are saved to `zones.json` and loaded again next time.

### Webcam demo ideas

- **Restricted zone:** press Z, draw a box on one side of the picture, ENTER, then lean into it.
- **Loitering:** stay in view for 20 s (or run with `--set loitering.seconds=8`).
- **Stealing:** put a phone, bottle or book on the table, wait until it shows `[guarded]`, then pick it up and walk off.
- **Fighting:** two people play-fighting (fast punches/pushes close together), or play a fight video with `--source`.

## Command-line options

| Option | Meaning |
|---|---|
| `--source X` | `0` = webcam (default), or a video file, an image, a folder of images, or a stream URL |
| `--rover URL` | use the rover: its camera, driving and patrol (default: `rover.url` in `config.yaml`) |
| `--mode M` | rover start mode: `manual`, `guard` or `patrol` |
| `--set key=value` | override any `config.yaml` value for this run, e.g. `--set fight.motion_threshold=6` (repeatable) |
| `--debug` | show per-person arm-motion values |
| `--save out.mp4` | also write the annotated video to a file |
| `--headless` | no window (for servers / automated tests) |
| `--max-frames N` | stop after N frames |

## Tuning (`config.yaml`)

- Too many false fight alerts → raise `fight.motion_threshold` (check normal `m=` values with `--debug`).
- Fights missed → lower `fight.model_threshold` (e.g. 0.5); too many fight alarms → raise it. (With `fight.method: rules`: `fight.motion_threshold`.)
- Too slow → lower `models.imgsz` (e.g. 416) or raise `models.object_every_n_frames`.
- CCTV / rover camera that can see people's feet → `zones.anchor: feet`.
- Send alerts and live view to the backend/dashboard → `backend.url: http://localhost:8080` (start it with `python -m backend`).

## Results

Measured on an Intel i7-8569U laptop, CPU only: pose ≈ 50 ms/frame at 480 px, objects ≈ 75 ms every 3rd frame → **about 10–12 FPS**.

Fight model (trained GRU) vs. the hand-tuned rule on the 400 RWF-2000 test clips: see [`docs/TRAINING.md`](docs/TRAINING.md#6-results) and `reports/fight_metrics.json`.

Fight rule (baseline):

- Walking video (OpenCV pedestrian sample, 795 frames): **0 false fight frames** at `motion_threshold: 5`.
- 30 RWF-2000 fight clips: detected in **16 of the 21 clips** where the pose model sees both people. In the other 9 clips (dark or top-down CCTV) the person detector finds fewer than two people, so no pose-based method could fire.

Known limitations:

- The trained fight model gives **1 false alert** on the walking video (about 54 s in), where two people stand shoulder to shoulder and their skeletons overlap. The fix is more NonFight training clips of people standing close together (`docs/TRAINING.md` section 8). Raising `fight.model_threshold` to 0.98 also avoids it but misses more fights.

- In a single **image** there is no motion, so fighting is only checked from pose (an extended arm hitting another person) and reported as `POSSIBLE FIGHT`.
- Stealing can false-alarm when someone stands right in front of a guarded object with a hand near it.
- Arms only are used for fight motion, since legs swing fast during normal walking, so kick-only fights are weaker.

## Project layout

```
run.py                   main app (webcam / video / images / rover)
config.yaml              all thresholds and settings
firmware/rover_arduino/  Arduino Uno firmware (motors, sonar, servo, GPS, I2C, Bluetooth)
rover/                   Raspberry Pi agent (camera stream + drive commands), I2C test, systemd service
backend/                 FastAPI backend + SQLite + dashboard (python -m backend)
docs/                    HOW_IT_WORKS_AND_DEMO.md, SETUP_GUIDE.md (wiring → environments → tests), TRAINING.md,
                         TESTING.md, MOBILE_APP_PLAN.md, ROADMAP.md
training/                extract_pose.py (videos → skeletons), train_fight.py (train + evaluate + plots)
params.yaml, dvc.yaml    training settings and the DVC pipeline
reports/                 metrics and plots of the last training run
rover_ai/
  vision.py              YOLO pose + object detection + tracking
  tracks.py              per-person keypoint history and arm-motion energy
  rules.py               loitering + restricted-zone rules
  learned.py             trained fight model: features, GRU, live pair scoring
  fight.py               rule-based fight detector (baseline / fallback, single-image check)
  theft.py               object guarding / taking detector
  alerts.py              cooldown, snapshot, clip, JSON log, sound, backend POST
  zones.py               zone storage and on-screen zone editor
  patrol.py              rover behaviour: patrol (default) / guard / manual; scene vs. obstacle
  rover_client.py        talks to the Pi agent
  services.py            stream reader, live view server, backend link
  draw.py                on-screen overlay
  geometry.py            keypoint helpers
tests/
  images/                sample images
  videos/                test videos (downloaded by tools/get_test_videos.py)
  zone_road.json         restricted zone used in test 3
  test_theft_sim.py      stealing logic test
  test_protocol.py       Arduino <-> Pi message format test
  test_learned.py        fight model features / resampling test
  test_patrol.py         patrol behaviour test (obstacle vs. scene, watch limit, alert hold)
tools/
  get_test_videos.py     downloads the test videos
  rover_drive.py         drive the rover once from the laptop (calibration)
  stream_rwf_sample.py   pulls N clips of RWF-2000 without downloading all 12 GB
  make_demo_reel.py      builds the demo video (normal / fight / loitering scenes) for the video-based demo
models/                  YOLO weights (downloaded) + fight_gru.pt/.json (trained here)
```

## Datasets and credits

- **RWF-2000** (fighting): Cheng, Cai & Li, *RWF-2000: An Open Large Scale Video Database for Violence Detection*, ICPR 2020. Hugging Face mirror: `DanJoshua/RWF-2000`. Research use only; clips are not included in this repository.
- **OpenCV `vtest.avi`** (walking test video), from the OpenCV samples.
- **Ultralytics YOLO11** models and tracking (AGPL-3.0).

## Next steps

- Retrain the fight model with labelled clips from the rover's own camera (`docs/TRAINING.md` section 8); scheduled retraining and CI.
- Pose-anomaly model (STG-NF) for general unusual behaviour.
- Native Android app (Kotlin) on top of the backend API, with an alert notification even when the app is closed (`docs/MOBILE_APP_PLAN.md`).
- Repeat the video-based full run through the rover's camera for the final evaluation (`docs/TESTING.md` Level 10).
