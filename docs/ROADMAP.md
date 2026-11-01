# AI Surveillance Rover – Roadmap from 0 to 100

This is the full plan to finish the project: what runs where, what to build next, how to test it, where to demonstrate it, and how it maps to the FYP report.

---

## 0. Where the project stands

| Part | Status |
|---|---|
| Rover hardware assembled and wired (as in the sample thesis, Figures 3.1 and 3.2) | ✅ Done (you); apply the 2 corrections in `SETUP_GUIDE.md` Part B |
| Individual components tested | ✅ Done (you) |
| AI detection: fighting, stealing, loitering, unauthorised access (`run.py`) | ✅ Done and tested on laptop |
| Arduino firmware (`firmware/rover_arduino`) | ✅ Written, compiles for Uno; to flash: `SETUP_GUIDE.md` C.2 |
| Pi software (`rover/rover_agent.py`) | ✅ Written, tested on the laptop with a simulated Arduino; on the Pi: `SETUP_GUIDE.md` C.3 |
| Laptop: detection on the rover's camera + patrol behaviour | ✅ Written, tested end-to-end with the simulated rover |
| FastAPI backend + database + web dashboard (`backend/`) | ✅ Written, tested in the browser (login, live view, alerts, clips, commands) |
| Android app | ⬜ Phase E |
| Trained fight model + MLOps (DVC, MLflow, retraining, CI) | 🟡 Phase F: model trained on RWF-2000 and used live; scheduler + CI still to do |
| Evaluation with your own recorded scenarios | ⬜ Phase G |
| Report + demo | ⬜ Phase H (the mid-term report is due first, see section 6) |

---

## 1. Final system: what runs where

The Raspberry Pi 3 is too slow to run the AI itself (about 1–3 FPS at best). So the Pi only **captures video and passes commands**, and the **laptop does all the AI**. This is the "offload" option already allowed in Section 3.2 of the proposal.

```
 ┌──────────────── ROVER ─────────────────┐            ┌──────────────── LAPTOP (on the same Wi-Fi) ─────────────────┐
 │                                        │            │                                                              │
 │  Pi Camera ──► Raspberry Pi 3B+        │  video     │  rover_ai detector (run.py)                                  │
 │                rover_agent.py  ────────┼──(MJPEG)──►│    YOLO11n-pose + ByteTrack + rules → alerts                 │
 │                  ▲      │ I2C          │            │          │                                                   │
 │   drive commands │      ▼              │  commands  │  patrol controller ──────────────────────┐                   │
 │   (HTTP) ────────┘   Arduino Uno ◄─────┼────────────│    stop-and-scan patrol, stop on alert   │                   │
 │                      ├ L298N + 4 DC motors          │          │ alerts (JSON)                  │                   │
 │                      ├ SG90 servo + HC-SR04 sonar   │          ▼                                │                   │
 │                      ├ GPS (NEO-6M)                 │  FastAPI backend ── SQLite ── alerts/ (snapshots + clips) │
 │                      └ HC-05 Bluetooth (manual)     │          │ REST + push                                         │
 └────────────────────────────────────────┘            └──────────┼───────────────────────────────────────────────────┘
                                                                  ▼
                                                     Android app: live view, alerts, history, rover status/control
```

| Device | Runs | Job |
|---|---|---|
| Arduino Uno | `firmware/rover_arduino/rover_arduino.ino` | Motors, obstacle stop (sonar), servo scan, GPS. Takes orders from the Pi over I2C. Stops on its own if orders stop arriving. |
| Raspberry Pi 3B+ | `rover/rover_agent.py` | Streams the Pi Camera over Wi-Fi; receives drive commands from the laptop and passes them to the Arduino; reports status (distance, GPS). |
| Laptop | `run.py` + `patrol.py` + `backend/` | AI detection on the live stream, patrol logic, alert storage, API for the app, training/MLOps. |
| Phone | Android app | Watch, get alerts, review incidents, check/stop the rover. |

> Step-by-step instructions for everything below: **`docs/SETUP_GUIDE.md`**.

> Update the report to match what you built: inference is **offloaded to the laptop** (justify it with the Pi 3 benchmark), and the Pi–Arduino link is **I2C**, not serial. Do the same for any proposal item you don't have (e.g. pan-tilt camera, LED).

---

## 2. Hardware checks (do these before writing more code)

These come from the sample thesis wiring and can stop things working or damage parts.

### 2.1 I2C pins: follow the figure, not the text
The sample text says "GPIO 2 → SCL, GPIO 3 → SDA". That is **swapped**. On the Pi, GPIO 2 is SDA and GPIO 3 is SCL (Figure 3.2 shows it correctly). Correct wiring for an **Uno**:

| Raspberry Pi (physical pin) | Arduino Uno |
|---|---|
| Pin 3 (GPIO 2, SDA) | A4 (SDA) |
| Pin 5 (GPIO 3, SCL) | A5 (SCL) |
| Pin 6 (GND) | GND |

Test from the Pi: `sudo apt install -y i2c-tools && i2cdetect -y 1` should show the Arduino's address (`08`) once the firmware is loaded.

### 2.2 5 V Arduino vs 3.3 V Pi
The Pi's pins are **3.3 V only**. The Arduino's I2C library turns on pull-ups to **5 V**, which pushes 5 V into the Pi.
- **Best:** put a cheap **4-channel bidirectional logic level converter** (BSS138 type) between them.
- **Without it:** it usually still works, because the Pi's strong 3.3 V pull-ups hold the lines near 3.4 V, but that's slightly above the Pi's specification.

### 2.3 HC-05 Bluetooth on pins 0/1
- Pins 0/1 are the same serial line as USB. **Unplug HC-05 RX/TX before uploading code** or the upload fails.
- HC-05 RX is 3.3 V: use a voltage divider (1 kΩ + 2 kΩ) from Arduino TX → HC-05 RX.

### 2.4 Servo library vs motor speed pins
On the Uno, the standard `Servo` library disables PWM on **pins 9 and 10**, and ENA is on pin 10. The firmware therefore drives the servo with its own pulses (no `Servo` library), so motor speed control keeps working.

### 2.5 GPS indoors
GPS modules **do not get a fix indoors**. Indoors the app shows "No GPS fix", which is fine. Show GPS only if you demo outdoors (open sky; the first fix can take 1–5 minutes).

### 2.6 Power
- Pi on the **power bank** (5 V, 2.5 A+), motors on the **battery** through the L298N.
- **All grounds connected together** (battery −, L298N GND, Arduino GND, Pi GND).
- Remove the L298N 5 V jumper if the motor battery is above 12 V.

---

## 3. Build phases

Each phase ends with a check you can see working before moving on.

### Phase A – Arduino firmware
One sketch, `firmware/rover_arduino/rover_arduino.ino`:
- **I2C slave at address 0x08.** Commands from the Pi: forward, back, left, right, stop, speed, servo angle.
- **Status** sent back to the Pi when asked: sonar distance, GPS fix, latitude, longitude.
- **Safety:**
  - Stop if no command arrives for 1 s (dead-man).
  - Refuse to drive forward if the sonar sees an obstacle under 25 cm.
- **Servo sweep** to find the clearer side when blocked.
- **HC-05:** optional manual driving from a Bluetooth terminal app (same command letters).

✅ **Check:** from the Pi, `python3 rover/i2c_test.py` drives forward 1 s, turns, stops, and prints the distance and GPS.

### Phase B – Pi rover agent
`rover/rover_agent.py` (Raspberry Pi OS, Python 3):
- `GET /stream.mjpg`: the Pi Camera as MJPEG, 640×480 @ 15 fps (`picamera2`).
- `POST /drive`: `{"cmd": "forward", "ms": 500, "speed": 150}` → I2C to the Arduino.
- `GET /status`: distance, GPS, battery/uptime.
- Starts automatically on boot (systemd service). The rover is ready as soon as you power it on.

✅ **Check:** open `http://<pi-ip>:8000/stream.mjpg` in the laptop browser and see live video. Then drive the rover from the browser.

### Phase C – Laptop: detection on the rover + patrol
- `run.py --source http://<pi-ip>:8000/stream.mjpg` works unchanged (OpenCV reads MJPEG over HTTP).
- `patrol.py`, the **stop-and-scan** patrol:
  1. drive forward ~1 m (the sonar stops it at obstacles);
  2. stop;
  3. turn 90° in place 4 times, watching ~3 s at each heading;
  4. repeat.
- On any alert: stop the rover, keep the camera on the scene, send the alert to the backend.
- Why stop-and-scan: detection works much better when the camera is still (no motion blur, steady tracks).

✅ **Check:** the rover patrols a room by itself. When a friend appears it stops and watches; if they stay (loitering) an alert appears.

### Phase D – FastAPI backend + dashboard
`backend/` on the laptop:
- **SQLite tables:**
  - `users`
  - `rovers`
  - `alerts` (type, time, track ids, snapshot, clip)
  - `telemetry`
- **Endpoints:**
  - `POST /auth/login` (JWT)
  - `POST /alerts` (the detector already sends here: set `alerts.backend_url`)
  - `GET /alerts`, `GET /alerts/{id}/clip`
  - `GET /rover/status`, `POST /rover/command`
  - `GET /live` (stream proxy)
- **Simple web dashboard:** live view + alert list + playback (also works in the phone browser). This is your fallback if the Android app isn't finished.
- **Push notifications:** Firebase Cloud Messaging (free) when an alert is stored.

✅ **Check:** trigger an alert → it appears in the dashboard with snapshot and clip within ~2 s.

### Phase E – Android app (Kotlin)

**Status:** planned. The full design is in [`MOBILE_APP_PLAN.md`](MOBILE_APP_PLAN.md).
- **Screens:** login, home (live view, status, Patrol/Guard/Manual, drive pad, STOP), alerts list, alert detail (snapshot + clip + real/false label), settings.
- **Alerts always on the phone:** a background service watches the backend over the local hotspot and posts a notification for every alert, even when the app is closed. No internet or Firebase is needed.
- The rover already starts patrolling by itself; the app shows what it's doing and changes the mode.

✅ **Check:** phone locked → the fight scene of the demo reel plays → notification arrives within 5 s → tap → see the clip.

### Phase F – Trained model + MLOps (the "research" part)

**Status:** steps 1–4 are done for RWF-2000: `training/extract_pose.py` → `training/train_fight.py`, settings in `params.yaml`, pipeline in `dvc.yaml`, optional MLflow logging, model in `models/fight_gru.pt`, used live by default. Details and results: [`TRAINING.md`](TRAINING.md). Still to do: rover clips (step 1b), the scheduler (5) and CI (6).
1. **Data:**
   - Pose sequences extracted from **RWF-2000** (fight / non-fight)
   - Your own **recorded rover clips** (labelled with Label Studio/CVAT)
2. **Model:** a small **GRU** on 20-frame (2 s) keypoint windows of two nearby people. Compare it with the current rule: accuracy, precision, recall, false alarms per hour.
3. **DVC pipeline** (`dvc.yaml`): `ingest → extract_pose → make_windows → train → evaluate → export (ONNX)`.
4. **MLflow:** every run logs data version, parameters, metrics; a model is promoted only if it beats the current one.
5. **Scheduler:** weekly (or when enough new labelled clips exist) `dvc repro` runs by itself.
6. **CI:** GitHub Actions runs the tests (`tests/`) on every push.

✅ **Check:** add new labelled clips → the scheduled run retrains → MLflow shows the new run → the better model is used automatically.

### Phase G – Evaluation (numbers for the report)
No real people act. Two sources of numbers:

1. **Offline accuracy:** 400 unseen RWF-2000 test clips (done: `TRAINING.md` section 6, report Table 4.6).
2. **Live through the rover's camera:** the demo reel played on a screen, full run repeated 3 times at different distances and lighting (`TESTING.md` Level 10).

| Reel scene | Runs | Detected | Missed | False alarms | Notes |
|---|---|---|---|---|---|
| Normal activity | | | | | |
| Fighting (4 test clips) | | | | | |
| Loitering | | | | | |
| Restricted zone | | | | | |
| Stealing (own clip, optional) | | | | | |

Also measure:
- FPS on the laptop
- Video delay from the Pi
- Time from event → phone notification
- Battery life during a patrol

### Phase H – Final report and demo
See sections 4–6.

---

## 4. Environment: where to run and demonstrate it

| Item | Recommendation |
|---|---|
| **Place** | Indoors: a lab, corridor or hall with a **smooth floor** (the rover drives differently on rough surfaces) and good, even lighting |
| **Network** | A **dedicated Wi-Fi hotspot or pocket router** for laptop + Pi + phone. University Wi-Fi often blocks device-to-device traffic, so don't rely on it. |
| **Laptop** | On mains power, connected to a projector/screen showing the live detection window |
| **Rover** | Fully charged battery and power bank; Pi set to start `rover_agent` on boot |
| **Scene** | Open floor with a few boxes for the patrol, and a **second screen** at the rover camera's height playing the demo reel (`HOW_IT_WORKS_AND_DEMO.md` 4.4) |
| **People** | **None needed.** All behaviour is shown with video footage played to the rover's real camera |
| **GPS** | Only outdoors. Indoors, say it shows "No fix" by design. |
| **Backup** | `python run.py --source data/demo/demo_reel.mp4 --set loitering.seconds=10`, plus a screen recording of a successful full run |

## 5. Demo script for the viva (about 7 minutes)

Full step list: `HOW_IT_WORKS_AND_DEMO.md` section 4.5.

1. **(30 s)** Show the rover, name the parts, and show the architecture slide (section 1).
2. **(1.5 min)** Switch it on: it **starts patrolling by itself**. It drives, scans, avoids a box, and stops in front of the screen when people appear (scene, not obstacle). Live video shows on the projector.
3. **(1 min)** Guard mode, reel scene 1 (normal): no alert. Scene 2 (fighting, unseen test clips): FIGHT alert on the laptop and phone.
4. **(1 min)** Scene 3: draw a zone on the road → UNAUTHORISED ACCESS alerts; one person stays → LOITERING.
5. **(30 s)** (Optional) your stealing clip → STEALING alert.
6. **(1 min)** Phone: alert history → tap an alert → snapshot + clip; label it. Switch modes from the phone.
7. **(1 min)** Show the training pipeline and the results table (learned model vs. rules on 400 unseen clips).
8. **(30 s)** Limitations, honestly: video instead of real people, the shoulder-to-shoulder false alarm, small camera height.

## 6. FYP report

**Mid-term report (due first).** Your project is both research (AI models, datasets) and development (rover, backend, app). The **R&D template** (`FYP-I-Report-Template-Research & Development (1).docx`) fits best, because it has *Research Gap*, *Dataset Details* and *Algorithm/Model Selection*. Confirm with your supervisor.

| Template section | What to put |
|---|---|
| 1 Introduction | Surveillance problem, why patrolling rovers, objectives, scope (indoor, private premises, video-based demonstration), **Spiral or Agile** SDLC |
| 2.1 Literature review | Anomaly detection (UCF-Crime, STG-NF), violence detection (RWF-2000), pose estimation (YOLO-pose), tracking (ByteTrack), skeleton action recognition (ST-GCN), surveillance robots. **≥10 IEEE references** + summary table |
| 2.2 Research gap | Existing datasets/models assume fixed high CCTV and GPU servers; little on low-cost mobile rovers; few explainable systems combining rules + learned models |
| 2.3 Requirements | Functional: detect 4 activities, alert, store evidence, patrol, live view, app. Non-functional: ≥10 FPS, alert < 3 s, privacy, works offline/locally. + traceability matrix |
| 2.4 Use cases | Security operator: login, start/stop patrol, live view, view alerts, review clip, define zones. Rover: patrol, avoid obstacles, stream. System: detect, alert, retrain |
| 3 Design | Activity diagrams (patrol, alert handling, login), architecture diagram (section 1), database diagram (users, rovers, alerts, telemetry) |
| 3.4 Dataset details | RWF-2000 (2,000 clips, fight/non-fight), UCF-Crime subset, OpenCV pedestrian video, your rover recordings |
| 3.5 Model selection | YOLO11n-pose vs heavier models (speed on CPU: 50 ms/frame), rules vs GRU/ST-GCN, why pose-based |
| 4 Implementation | WBS, tools, what's built so far, **screenshots of the detector** (fight/zone/loitering alerts), challenges (Pi too slow → offloaded; walking false alarms → arms-only motion, threshold tuned on data) |

If your department expects the **Development** template instead, it additionally needs: sequence diagram, class diagram, collaboration diagram and test cases.

## 7. Suggested order and timeline

| Weeks | Work |
|---|---|
| Now → mid-term deadline | Mid-term report (section 6) + hardware checks (section 2) |
| +1–2 | Phase A (Arduino) + Phase B (Pi agent) |
| +3 | Phase C (live detection on the rover + patrol) – **first full demo possible here** |
| +4–6 | Phase D (backend + dashboard) |
| +7–10 | Phase E (Android app) |
| +8–12 (parallel) | Phase F (trained fight model + DVC/MLflow/CI) |
| +12–14 | Phase G (record test set, measure, results table) |
| +14–16 | Final report, poster, demo rehearsal |
