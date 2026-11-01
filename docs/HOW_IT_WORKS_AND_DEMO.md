# How the rover works, and how to demonstrate it

This page explains, step by step, how the whole system works and how the rover behaves, then how to **demonstrate everything with videos**, without asking real people to fight or act.

- Building and wiring: `SETUP_GUIDE.md`.
- The trained model: `TRAINING.md`.
- The phone app plan: `MOBILE_APP_PLAN.md`.

---

## Part 1: How the whole system works

### 1.1 The four parts and their jobs

| Part | Role | What it does |
|---|---|---|
| **Arduino Uno** (on the rover) | Muscles and reflexes | Drives the 4 motors through the L298N. Reads the sonar about 16 times a second and **refuses to drive forward into anything closer than 25 cm**, by itself. Moves the sonar servo, reads the GPS. |
| **Raspberry Pi 3B+** (on the rover) | Eyes and messenger | Streams the camera to the laptop over Wi-Fi. Passes driving commands from the laptop to the Arduino over I2C. It runs **no AI**: it is too slow for that. |
| **Laptop** | Brain | Runs all the AI (`run.py`) and the patrol brain that decides what the rover does. |
| **Backend + dashboard / phone** | Control room | Stores every alert with a snapshot and a 4-second video clip. Shows them on the phone and lets you drive the rover or change its mode. |

```
 Pi camera ─► Raspberry Pi ──Wi-Fi video──► LAPTOP: find people → track them → judge behaviour
                                                │                                   │
 Motors ◄── Arduino ◄──I2C── Raspberry Pi ◄─────┘ "stop / turn / hold"              └─► alert ─► phone
```

### 1.2 What happens to one camera picture (about 0.1 s)

1. **Camera.** The Pi takes a picture and sends it to the laptop as a JPEG.
2. **People.** YOLO11n-pose finds every person and their **17 body points**: nose, eyes, ears, shoulders, elbows, wrists, hips, knees and ankles.
3. **Identity.** ByteTrack gives each person an ID that stays the same from picture to picture ("ID 7 is the same person as a moment ago").
4. **History.** The last few seconds of every person's skeleton are kept.
5. **Behaviour.** The detectors look at that history:

   | Activity | How it is decided |
   |---|---|
   | **Fighting** | A **trained neural network** (GRU) reads the skeletons of two people who are close together over 2 seconds and outputs a fight probability. Alert when two judgements in a row are above 0.73. It was trained on 1,600 RWF-2000 clips and tested on 400 it never saw (`TRAINING.md`). |
   | **Loitering** | A person has been in view longer than the time you set (20 s by default). |
   | **Restricted zone** | A person's feet enter an area you drew on the picture (guard mode only, because the camera must not move). |
   | **Stealing** | A second model (YOLO11n objects) watches bags, laptops, phones and bottles. An object that lies still for 3 s becomes *guarded*. If a hand touches it and it then moves away or disappears, that is a stealing alert naming the person. |

6. **Alert.** The laptop does five things:
   - saves a snapshot and the last 4 s of video
   - shows a red banner and plays a sound
   - sends the alert to the backend, so it appears on the phone
   - tells the rover to **stop and hold position**, so the camera keeps recording
   - sends **one alert per incident**: three people fighting give one alert, not six (`alerts.type_cooldown_seconds`). The screen still marks everyone involved.

### 1.3 Why skeletons, and why a laptop

- **Skeletons.** They ignore clothes, background and lighting. They are tiny, so behaviour can be judged over time. They are also the same whether they come from a CCTV clip, a phone video or the rover's own camera, so a model trained on CCTV clips can run on the rover.
- **A laptop.** The Pi 3B+ manages only 1–3 pictures per second with these models. The laptop manages about 10, which is enough to judge movement.

---

## Part 2: How the rover behaves

### 2.1 It starts by itself in patrol

When the rover and `run.py` start, the rover **wanders by itself** (patrol mode, `rover.start_mode: patrol`). It doesn't move until the camera and the AI are both working, so it never drives blind. You can switch modes at any time from the phone or the keyboard:

| Mode | Behaviour |
|---|---|
| **Patrol** (default) | Wanders, looks around, stops to watch people, holds position after an alert |
| **Guard** | Parked: watches one scene. Restricted zones work only here |
| **Manual** | Moves only when you drive it (phone arrows or W/A/S/D) |

### 2.2 The patrol loop

```
 ┌─► Alert in the last 20 s? ── yes ─► HOLD position, keep recording (and watch the scene again afterwards)
 │        │ no
 │   Person seen in the last 5 s? ── yes ─► STOP and WATCH (up to 60 s without an alert, then move on)
 │        │ no
 │   Drive forward one step (1.2 s)  ×3 ──► something in front? (see 2.3)
 │        │
 │   Look around: 4 directions, 4 s each, turning 90° between them
 └────────┘
```

- **Watching** is what makes the rover useful. Fighting, stealing and loitering can only be judged from a camera that is standing still.
- After watching the same people for 60 s with nothing happening, it **moves on** and ignores them for 20 s so it really leaves (`rover.investigate_max_s`, `rover.move_on_s`). An alert still stops it at any time.
- **After an alert**, it holds still for 20 s and then watches for a full period again. It never drives away right after something happened.

### 2.3 Something in front: scene or obstacle?

The rover must **not** simply turn away from everything in front of it. It might be the very scene it should record. So:

1. The **Arduino** always refuses to drive forward into something closer than 25 cm. This is the safety reflex and works even if the laptop crashes.
2. Before turning away, the rover **looks for 1.5 s** (`rover.look_before_turn_s`):
   - **People in view, or an alert active:** what's in front is part of a **scene**. It **stays and records**.
   - **Nothing of interest in view:** it's a **blocked path**. The sonar sweeps left and right and the rover turns toward the clearer side. If both sides are closer than 40 cm, it backs up and turns around.
3. When the scene is over and the path is still blocked, it turns then.

### 2.4 Safety

- Every driving command has a time limit, for example "forward for 1.2 s". If Wi-Fi drops or the laptop stops, **the Arduino stops the motors by itself**.
- Pressing **Q** in the detection window or **■** on the phone stops the rover immediately.

---

## Part 3: How alerts reach you

| Now (works today) | Planned: Android app (`MOBILE_APP_PLAN.md`) |
|---|---|
| Laptop: red banner and sound | Same |
| Phone browser dashboard: list of alerts with snapshot and clip; beeps, vibrates and shows a banner for new alerts **while the page is open**; buttons for patrol / guard / manual and driving | Native app: the same controls, **plus a notification for every alert even when the app is closed or the phone is locked**, and a warning if the rover goes offline |

---

## Part 4: Demonstration without real people

### 4.1 Why videos

- The rover is small. Its camera is about 15 cm above the floor.
- Staging a fight with real people is unsafe and not appropriate.
- Recording people who haven't agreed isn't acceptable either.

So **all behaviour is demonstrated with video footage that the rover's real camera films from a screen**. Everything after the camera is the real system: Pi stream → laptop AI → alert → phone → rover stops and holds. **Accuracy** comes from the test on 400 unseen RWF-2000 clips (`TRAINING.md`, report Table 4.6), not from the demo.

### 4.2 What is shown, and how

| What | How it is shown | Expected result |
|---|---|---|
| Normal activity | Reel scene 1: people close together, not fighting (RWF-2000 test clips) | **No** alert |
| Fighting | Reel scene 2: 4 RWF-2000 **test** clips, never used in training | **FIGHT** alert naming two IDs; rover holds position |
| Loitering | Reel scene 3: pedestrian video; one person stays in view | **LOITERING** after 10 s (demo setting) |
| Restricted zone | Reel scene 3 in **guard** mode, zone drawn on the road | **UNAUTHORISED ACCESS** for each person entering |
| Stealing | Reel scene 4 (optional): your own phone clip, see 4.3 | **STEALING** alert |
| Patrol | The rover drives around the room by itself | Short steps, looks around in 4 directions |
| Obstacle | A box in its path | Stops at about 25 cm, looks, then turns toward the free side |
| Scene in front | The screen with people on it, right in front of the rover | Stops and **stays**: it does not turn away |
| Watching | The rover turns toward the screen while people are on it | Stops and watches (status `watching people`) |
| Phone | During all of the above | Alerts with snapshot and clip; mode buttons work |

### 4.3 Make the demo reel (once)

```bash
source .venv/bin/activate
python tools/make_demo_reel.py
#  -> data/demo/demo_reel.mp4 (about 110 s) and data/demo/demo_reel.txt (scene times)
```

- It needs `data/rwf/RWF-2000.tar.gz` (the clips are taken out once) and `tests/videos/walking.avi` (`python tools/get_test_videos.py`).
- Each scene starts with a 4-second title card that says what should happen, so the audience can follow. Between clips there is a 3 s blank pause, so one clip's people are never mixed up with the next.
- **Stealing scene (optional).** With your phone, record 10–15 s of a bag, phone or laptop lying still on a table for 5 s, then a hand picks it up and takes it out of view. Only a hand is needed. Then run:
  ```bash
  python tools/make_demo_reel.py --stealing my_stealing_clip.mp4
  ```

**Checked in software** (`python run.py --source data/demo/demo_reel.mp4 --set loitering.seconds=10`):
- scene 1: no alerts
- scene 2: a FIGHT alert for every fight clip; live, one FIGHT alert per 10 s, so 3–4 alerts
- scene 3: LOITERING at about 81 s

Also checked live, with the simulated rover streaming the reel: the rover starts patrolling by itself, stops when people appear, and gets the FIGHT and LOITERING alerts. Scene 1 gives no alert.
- with a zone drawn on the road: UNAUTHORISED ACCESS alerts

### 4.4 Set up the stage

```
                    ┌──────────────────────┐
                    │   screen / laptop    │  ← full screen, full brightness,
                    │   playing the reel   │    centre at the rover camera's height
                    └──────────────────────┘    (put it on the floor or a low box)
                               ▲
                               │  0.8 – 1.2 m   (the screen should fill most of the camera picture)
                               │
                          ┌─────────┐
                          │  rover  │  camera pointing straight at the screen
                          └─────────┘
      dim the room lights · no window or lamp reflecting in the screen · the bigger the screen, the better
```

- The screen is a **second device** (TV, monitor, tablet or a second laptop). You need the AI laptop's own window to see detections and draw zones.
- **Test it today without the rover:** run `python run.py` with the laptop webcam pointed at your phone or a second screen playing the reel.
- Look at the detection window: the people on the screen must get **skeletons**. If they don't, move closer, make the screen brighter, or reduce reflections.
- If you see stripes (flicker), change the screen brightness or the distance a little.

### 4.5 One full run = the whole system test

You don't have to test every part separately. **One 15-minute session shows and checks everything.** The hardware bring-up checks (`SETUP_GUIDE.md` F.1–F.4) are done once when you build the rover, only to catch wiring mistakes.

Start everything (`SETUP_GUIDE.md` Part E), with the demo loitering time:

```bash
python -m backend                                   # terminal 1
python run.py --set loitering.seconds=10            # terminal 2 (rover.url and backend.url set in config.yaml)
```

| Step | What you do | What should happen | ✔ |
|---|---|---|---|
| 1 | Power on, start both programs | Status `waiting for camera + AI`, then the rover starts **patrolling by itself** | |
| 2 | Watch for 2 minutes in a room with a few boxes | Short forward steps, looks in 4 directions, stops ≈25 cm before boxes, looks, turns to the free side, never hits anything | |
| 3 | Put a box right in its path | Stops, looks 1.5 s, turns toward the clearer side (**blocked path**) | |
| 4 | Start the reel (scene 2 or 3) on the screen and let the rover turn toward it, or carry it in front | Stops and **watches** (`watching people`) instead of driving on | |
| 5 | Put the screen **right in front** (closer than 25 cm) while people are on it | Stays and records, **does not turn away** (`something in front: recording the scene`) | |
| 6 | Phone: press **Guard**. Place the rover 1 m from the screen. Play the reel from the start | Rover stays parked | |
| 7 | Scene 1 (normal) | **No** alert | |
| 8 | Scene 2 (fighting) | **FIGHT** alert on the laptop and on the phone, with snapshot and clip | |
| 9 | Scene 3 card: pause, press **Z**, click the corners of the road, Enter. Play on | **UNAUTHORISED ACCESS** alerts as people enter; **LOITERING** after ≈10 s | |
| 10 | Press **C** (clear the zone), then **Patrol**. Replay scene 2 | FIGHT alert → rover **holds position** (`alert: holding position`) for 20 s, then watches again | |
| 11 | (Optional) Stealing scene | **STEALING** alert | |
| 12 | Phone: open the alerts, play a clip, mark one **real** and one **false alarm**. Press ▲ ◀ ■ and Manual | Clips play; labels saved; rover obeys within 1–2 s | |
| 13 | Switch off the hotspot for 10 s while it drives | Rover **stops by itself** within 3 s | |

Write the result of each step in the ✔ column, and take a phone video of the whole session. That video and this table are your system test and your demo evidence.

### 4.6 Backup plan (if Wi-Fi or hardware fails on the day)

```bash
python run.py --source data/demo/demo_reel.mp4 --set loitering.seconds=10
```

The same alerts appear on the laptop and the phone dashboard, without the rover. Also keep a **screen recording of a successful full run** on the laptop.

### 4.7 What to say to the examiners

> "The detection accuracy was measured on 400 unseen RWF-2000 test clips: 74% accuracy where two people are visible, against 62% for hand-tuned rules (Table 4.6). Because the rover is small and staging real fights is unsafe, the live demo uses recorded footage played to the rover's real camera. Everything from the camera to the phone alert is the real system."

### 4.8 What to expect (honest notes)

- **One alert per incident.** A fight between several people gives one alert, while the screen marks everyone involved.
- **Known false alarm.** Two people standing or walking **shoulder to shoulder**, with overlapping skeletons, can give a false FIGHT alert. This is reported in the report (TC-05). The fix is more "normal" training clips of people close together.
- **The restricted zone is a fixed area of the picture**, so draw it only for the zone part of the demo and clear it afterwards (C).
- **Video of a screen is harder than real life:** reflections, smaller people. If one fight clip doesn't trigger, the others will. Keep the screen bright and close.
