# AI Surveillance Rover – Suspicious Activity Detection

Real-time detection of **fighting, stealing, loitering and unauthorised access** from a webcam, video file or images. Runs on an ordinary laptop CPU, no GPU or training needed.

This is the detection part of an AI-powered surveillance rover (final-year project). The rover streams its camera to this software, which raises alerts that can be forwarded to a FastAPI backend and an Android app.

## What it detects

| Activity | How | Training needed |
|---|---|---|
| People, body pose, ID tracking | YOLO11n-pose + ByteTrack (pretrained on COCO) | No |
| Loitering | Same person in view (or inside a loiter zone) longer than N seconds | No |
| Unauthorised access | A person enters a restricted zone you draw on screen | No |
| Fighting | Two people close together + fast arm motion relative to the body + a hand reaching the other person, held over several frames | No (rule tuned on RWF-2000 clips) |
| Stealing | An object (bag, laptop, phone, bottle…) sits still and becomes "guarded"; a hand touches it and it is moved away or disappears | No (pretrained YOLO11n object model) |

```
camera / video ──► YOLO11n-pose + ByteTrack ──► per-person keypoint history ──┬─► loitering rule
                   YOLO11n objects (every 3rd frame) ──► object states ───────┤─► restricted-zone rule
                                                                              ├─► fight detector
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

**Expected:** the two people fighting in the right corner turn **red** with **`FIGHT`**, while bystanders stay green. `--debug` shows each person's arm-motion value `m=`: fighters go above 5, people standing around stay near 1–2.

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
 "snapshot": "alerts/20261031/20261031_074242_fight-9-15.jpg", "clip": "alerts/20261031/20261031_074242_fight-9-15.mp4"}
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
| D | show/hide debug values |
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
| `--source X` | `0` = webcam (default), or a video file, an image, or a folder of images |
| `--set key=value` | override any `config.yaml` value for this run, e.g. `--set fight.motion_threshold=6` (repeatable) |
| `--debug` | show per-person arm-motion values |
| `--save out.mp4` | also write the annotated video to a file |
| `--headless` | no window (for servers / automated tests) |
| `--max-frames N` | stop after N frames |

## Tuning (`config.yaml`)

- Too many false fight alerts → raise `fight.motion_threshold` (check normal `m=` values with `--debug`).
- Fights missed → lower `fight.motion_threshold` or `fight.min_positive_frames`.
- Too slow → lower `models.imgsz` (e.g. 416) or raise `models.object_every_n_frames`.
- CCTV / rover camera that can see people's feet → `zones.anchor: feet`.
- Send alerts to the backend → `alerts.backend_url: http://localhost:8000/alerts` (JSON POST).

## Results

Measured on an Intel i7-8569U laptop, CPU only: pose ≈ 50 ms/frame at 480 px, objects ≈ 75 ms every 3rd frame → **about 10–12 FPS**.

Fight rule:

- Walking video (OpenCV pedestrian sample, 795 frames): **0 false fight frames** at `motion_threshold: 5`.
- 30 RWF-2000 fight clips: detected in **16 of the 21 clips** where the pose model sees both people. In the other 9 clips (dark or top-down CCTV) the person detector finds fewer than two people, so no pose-based method could fire.

Known limitations:

- In a single **image** there is no motion, so fighting is only checked from pose (an extended arm hitting another person) and reported as `POSSIBLE FIGHT`.
- Stealing can false-alarm when someone stands right in front of a guarded object with a hand near it.
- Arms only are used for fight motion, since legs swing fast during normal walking, so kick-only fights are weaker.

## Project layout

```
run.py                   main app (webcam / video / images)
config.yaml              all thresholds and settings
rover_ai/
  vision.py              YOLO pose + object detection + tracking
  tracks.py              per-person keypoint history and arm-motion energy
  rules.py               loitering + restricted-zone rules
  fight.py               fight detector (video and single-image versions)
  theft.py               object guarding / taking detector
  alerts.py              cooldown, snapshot, clip, JSON log, sound, backend POST
  zones.py               zone storage and on-screen zone editor
  draw.py                on-screen overlay
  geometry.py            keypoint helpers
tests/
  images/                sample images
  videos/                test videos (downloaded by tools/get_test_videos.py)
  zone_road.json         restricted zone used in test 3
  test_theft_sim.py      stealing logic test
tools/
  get_test_videos.py     downloads the test videos
  stream_rwf_sample.py   pulls N clips of RWF-2000 without downloading all 12 GB
models/                  YOLO weights (auto-downloaded)
```

## Datasets and credits

- **RWF-2000** (fighting): Cheng, Cai & Li, *RWF-2000: An Open Large Scale Video Database for Violence Detection*, ICPR 2020. Hugging Face mirror: `DanJoshua/RWF-2000`. Research use only; clips are not included in this repository.
- **OpenCV `vtest.avi`** (walking test video), from the OpenCV samples.
- **Ultralytics YOLO11** models and tracking (AGPL-3.0).

## Next steps

- Train a small GRU / ST-GCN on pose sequences extracted from RWF-2000 and compare it with the rule-based fight detector.
- Connect `alerts.backend_url` to the FastAPI backend and Android app.
- Record test footage from the rover's own camera to tune thresholds for its viewpoint.
