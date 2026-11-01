# Testing Guide – from 0 to 100

The physical-rover tests are also written out step by step, with "if it fails" hints, in `SETUP_GUIDE.md` Part F.

**No real people have to act.** All behaviour is tested with video played on a screen in front of the rover's camera (`HOW_IT_WORKS_AND_DEMO.md` Part 4). Accuracy comes from the 400 unseen RWF-2000 test clips (`TRAINING.md`).

**Short path: you don't have to run every test separately.** The minimum is:
1. Level 1: software quick checks (10 min).
2. Levels 3–5: hardware bring-up, **once**, while building, to catch wiring mistakes.
3. **Level 7: one full run with the demo video.** It checks every activity, the rover's behaviour and the phone in one session.

The other levels are there when something fails and you need to find which part caused it, or for extra measurements for the final report.

| Level | What | Needs hardware? | Time |
|---|---|---|---|
| 0 | Preparation | – | 15 min |
| 1 | Detection software on the laptop | No | 20 min |
| 2 | Whole system with a simulated rover | No | 20 min |
| 3 | Arduino alone | Arduino + rover | 30 min |
| 4 | Raspberry Pi alone | Pi + camera | 30 min |
| 5 | Pi + Arduino together | Rover | 30 min |
| 6 | Full system with the real rover | Rover + laptop + phone | 45 min |
| 7 | **One full run with the demo video** (all activities) | Rover + laptop + phone + a screen | 15 min |
| 8 | Robustness (things going wrong) | Full system | 30 min |
| 9 | Measurements for the report | Full system | 1 h |
| 10 | Results table (repeated full runs + RWF-2000 test) | Full system + a screen | 45 min |

Test IDs **TC-01…TC-14**, **TC-59**, **TC-61** and **TC-62** match Table 4.8 of the mid-term report; new IDs continue from TC-15.

---

## Level 0 – Preparation

1. Open a terminal in the project folder and activate the environment:
   ```bash
   cd ~/Desktop/"Ahmad Rover"
   source .venv/bin/activate
   ```
2. Make a **test log** (spreadsheet or notebook) with these columns, and fill in one row per test:

   | Date | Test ID | What I did | Expected | What happened | Pass / Fail | Notes / screenshot file |
   |---|---|---|---|---|---|---|

3. Keep every screenshot and clip: they go into the final report. Alerts are saved automatically in `alerts/`.

---

## Level 1 – Detection software (no hardware)

| ID | Command | Pass if |
|---|---|---|
| TC-01 | `python run.py --source tests/images` | Both images show people with skeletons (press a key to go to the next) |
| TC-02 | `python run.py --source tests/videos/walking.avi --set loitering.seconds=8` | Banner **LOITERING: ID 45 in view for 8s** appears after ~30 s |
| TC-03 | `python run.py --source tests/videos/walking.avi --set zones.file=tests/zone_road.json --set zones.anchor=feet --set loitering.enabled=false` | People entering the red zone get **UNAUTHORISED ACCESS** alerts |
| TC-04 | `python run.py --source tests/videos/fight.avi --set loitering.enabled=false --debug` | The two fighters (IDs 9, 15) turn red with **FIGHT**; bystanders stay green |
| TC-05 | Run TC-02's command and watch the whole video | **No** FIGHT alert on normal walking. *Known result:* the rules give 0; the trained model gives 1 false alert at ~54 s, where two people stand shoulder to shoulder. Recorded as an open issue (fix: add such scenes as NonFight clips and retrain) |
| TC-06 | `python tests/test_theft_sim.py` | 3 × **[PASS]** |
| TC-13 | `python tests/test_protocol.py` | **[PASS] Arduino status decodes correctly** |
| TC-60 | `python tests/test_learned.py` | 9 × **[PASS]** (features, model, live resampling) |
| TC-61 | `python tests/test_patrol.py` | 5 × **[PASS]** (blocked path → turn; scene in front → stay; move on after 60 s; hold after alert; alert restarts watching) |
| TC-62 | `python tools/make_demo_reel.py`, then `python run.py --source data/demo/demo_reel.mp4 --set loitering.seconds=10` | Scene 1: no alert; scene 2: a FIGHT alert for each fight clip (live: one per 10 s); scene 3: LOITERING (times in `data/demo/demo_reel.txt`) |
| TC-59 | `python -c "import json; m=json.load(open('models/fight_gru.json')); print(m['test_learned'], m['test_rules'])"` | The trained model's F1 on the 400 RWF-2000 test clips is higher than the rules' (retrain: `docs/TRAINING.md`) |
| TC-15 | `python run.py` (laptop webcam) | You appear with a skeleton; press **Z**, draw a zone, lean into it → alert |

If the test videos are missing: `python tools/get_test_videos.py`.

---

## Level 2 – Whole system with a simulated rover (no hardware)

This proves the software chain works before you add hardware. Use **three terminals**, each with the environment activated.

```bash
# Terminal 1 – a fake rover: plays the walking video as its "camera" and pretends to be the Arduino
python rover/rover_agent.py --fake --camera file:tests/videos/walking.avi --token test123 --port 8765

# Terminal 2 – backend + dashboard (first time only: python -m backend.manage create-user admin)
python -m backend

# Terminal 3 – detection connected to the fake rover and the backend
python run.py --rover http://127.0.0.1:8765 --set rover.token=test123 --set backend.url=http://127.0.0.1:8080
```

| ID | Do | Pass if |
|---|---|---|
| TC-07 | Look at the detection window | Live video from the fake rover, yellow status line `ROVER MANUAL …` |
| TC-10 | Press **G** (patrol) | Status shows *moving forward / scanning*, and *watching people* when someone is detected; terminal 1 prints `[fake arduino] F …` commands |
| TC-12 | Open `http://localhost:8080`, log in, press **Guard** | Within ~1 s the window's status line says `ROVER GUARD | parked` |
| TC-08 | Stop terminal 3, run `python run.py --source tests/videos/fight.avi --set backend.url=http://127.0.0.1:8080` | Fight alerts appear in the dashboard; clicking one plays the clip |
| TC-09 | In the dashboard log out, try a wrong password | "Wrong username or password"; nothing visible without logging in |
| TC-16 | Open the dashboard on your **phone** (same Wi-Fi, address printed by terminal 2) | Live view and alerts work on the phone |

Stop everything with Ctrl+C when done.

---

## Level 3 – Arduino alone

Follow `SETUP_GUIDE.md` Parts B and C.2 to wire and upload. **Wheels off the ground** for every motor test.

| ID | Do | Pass if |
|---|---|---|
| TC-17 | Upload, open Serial Monitor (9600) | `ROVER READY` printed, servo centres |
| TC-18 | Type `F`, `B`, `L`, `R`, `S` in the Serial Monitor | Forward / back / spin left / spin right / stop, each ≈0.3 s. Fix directions with `INVERT_A/B`, `SWAP_SIDES` (guide C.2 step 8) |
| TC-19 | Type `F` repeatedly with your hand 15 cm in front of the sonar | Wheels **refuse** to go forward (obstacle stop) |
| TC-20 | Type digits `2` then `F`, then `9` then `F` | Slow then fast (speed control on ENA/ENB works) |
| TC-21 | (Optional) Pair phone with HC-05, use an RC app | App buttons drive the rover |
| TC-22 | (Outdoors only) Leave powered under open sky 5 min | Later, in Level 5, `gps_fix: true` with sensible lat/lon |

---

## Level 4 – Raspberry Pi alone

Follow `SETUP_GUIDE.md` C.3.

| ID | Do | Pass if |
|---|---|---|
| TC-23 | `ssh pi@rover.local` | You get a prompt |
| TC-24 | `rpicam-hello --list-cameras` | Camera listed |
| TC-25 | `python3 ~/rover/rover_agent.py --fake --token X` then open `http://rover.local:8000/?token=X` on the laptop | Live camera in the browser (Arduino simulated) |
| TC-26 | Watch the browser video for 2 minutes | No freezing; picture updates smoothly (≈15 fps) |

---

## Level 5 – Pi + Arduino together

| ID | Do | Pass if |
|---|---|---|
| TC-27 | On the Pi: `i2cdetect -y 1` | `08` appears |
| TC-28 | On the Pi: `cd ~/rover && python3 i2c_test.py` (wheels off ground) | Status read; F/B/L/R each correct for 1 s; sonar sweep prints 3 distances; live distance follows your hand |
| TC-29 | `python3 rover_agent.py --token MY-SECRET` (real Arduino), drive with the browser buttons | Rover moves; `/status` shows changing `distance_cm` |
| TC-11 | Drive the rover (on the floor) towards a wall | It stops ≈25 cm from the wall by itself |
| TC-30 | Unplug the laptop/Wi-Fi while the rover is driving | Rover stops within 3 s (commands expire) |
| TC-31 | Install the service (guide F.3.5), reboot the Pi | Browser page works again after ~1 minute without logging in |

---

## Level 6 – Full system with the real rover

Set `rover.url`, `rover.token`, `backend.url` in `config.yaml` (guide E.1), then start the backend and `python run.py`.

| ID | Do | Pass if |
|---|---|---|
| TC-32 | Look at the detection window | Rover's live camera, status line shows sonar distance |
| TC-33 | Drive with **W/A/S/D**, stop with **Space** | Rover follows; stops when told |
| TC-34 | Calibrate: `python tools/rover_drive.py right 700`, adjust until ≈90°, save `rover.turn_90_ms` | Four turns ≈ one full circle |
| TC-35 | Camera tilt: a person 2–3 m away | Whole body visible (tilt camera up ≈20° if not) |
| TC-36 | Press **H** (guard), draw a zone with **Z** around a door | Zone shown in red |
| TC-37 | Press **G** (patrol) in an empty, closed room for 5 minutes | Drives in steps, scans 4 directions, turns away from walls, never hits anything, never leaves the area |
| TC-38 | Walk into view during patrol | Rover stops and watches; resumes ≈5 s after you leave |
| TC-39 | Press Patrol / Guard / arrows on the **phone dashboard** | Rover obeys within ~1–2 s |

---

## Level 7 – One full run with the demo video

Make the reel once (`python tools/make_demo_reel.py`), play it on a second screen about 1 m in front of the rover, and follow the **13 steps in `HOW_IT_WORKS_AND_DEMO.md` section 4.5**. The test IDs below are the parts of that run.

| ID | Part of the full run | Mode | Pass if |
|---|---|---|---|
| TC-40 | Reel scene 1: normal activity | Guard | **No** alert |
| TC-41 | Reel scene 3: one person stays in view (`--set loitering.seconds=10`) | Guard | LOITERING after ≈10 s |
| TC-42 | Reel scene 3 with a zone drawn on the road | Guard | UNAUTHORISED ACCESS when people enter |
| TC-43 | Reel scene 2: fighting (RWF-2000 test clips) | Guard | FIGHT alert naming two IDs |
| TC-44 | Reel scene 2 again in **patrol** | Patrol | FIGHT alert, then the rover **holds position** for 20 s |
| TC-45 | Reel scene 4 (optional, your own clip of a hand taking a bag) | Guard | STEALING alert |
| TC-46 | Box in the path / screen with people right in front | Patrol | Box: turns away. Screen with people: **stays and records** |
| TC-47 | Every alert above | – | Arrives on the phone with snapshot + playable clip; one alert per incident |

Afterwards, open each alert in the dashboard and label it **real** or **false alarm**.

---

## Level 8 – Robustness (what happens when things go wrong)

| ID | Do | Pass if |
|---|---|---|
| TC-48 | Stop the backend for 1 minute while the fight scene plays, then restart it | The alert still appears in the dashboard after restart (it was queued) |
| TC-49 | Switch off the Wi-Fi hotspot for 20 s during patrol | Rover stops; detection window says "Waiting for rover video"; everything resumes when Wi-Fi returns |
| TC-50 | Reboot the Pi during a session | Video and control come back within ~1–2 minutes without restarting the laptop programs |
| TC-51 | Brighter room / reflections on the screen / screen further away | Note at what point people on the screen stop being detected (limitation for the report) |
| TC-52 | Run until the motor battery is low | Note the battery life and whether turns get shorter (re-calibrate `turn_90_ms`) |

---

## Level 9 – Measurements for the report

| ID | Measure | How |
|---|---|---|
| TC-14 | Detector FPS | Read `FPS` in the detection window (or the dashboard) while the reel shows several people; note min / typical |
| TC-53 | Alert latency (detector → backend) | Compare `time` in `alerts/alerts.jsonl` with the `received` time in the dashboard database; both use the laptop clock |
| TC-54 | Alert latency (event → phone) | Film the screen and the phone together with a second phone; count frames from the first punch in the fight scene to the phone pop-up |
| TC-55 | Video delay (Pi → laptop) | Show a phone stopwatch to the rover camera and photograph it next to the detection window; the difference is the delay |
| TC-56 | False alarms | Count false alerts over all full runs (normal scenes should give none). The offline number is in Table 4.6: false alarms on the 200 RWF-2000 normal test clips |
| TC-57 | Battery life | Full charge → patrol until it stops moving reliably |
| TC-58 | Patrol coverage | Over 10 minutes, mark on a floor sketch where the rover went; estimate the % of the area seen |

---

## Level 10 – Results table

1. **Accuracy (offline)**: the RWF-2000 test result is already measured: `reports/fight_metrics.json`, report Table 4.6 (trained model vs. hand-tuned rules on 400 unseen clips).
2. **Live, through the rover's camera**: do the full run (Level 7) **3 times**, for example at 0.8 m and 1.2 m from the screen, and with the lights on and off. Count per scene:

| Reel scene | Expected | Runs | Correct alert | Missed | False alarms |
|---|---|---|---|---|---|
| 1 Normal | no alert | 3 | – | – | |
| 2 Fighting (4 clips) | FIGHT per clip | 3 × 4 | | | |
| 3 Loitering | LOITERING | 3 | | | |
| 3 Restricted zone | UNAUTHORISED ACCESS | 3 | | | |
| 4 Stealing (optional) | STEALING | 3 | | | |

3. **Add** the timing numbers from Level 9.
4. **Write up** in the final report: both tables, a short explanation of each miss and false alarm (with a screenshot), and what you would change (threshold, screen distance, more training data).

---

## Before the viva: final rehearsal checklist

- [ ] One full run (Level 7) passes on the demo day hardware, in the demo room
- [ ] Battery and power bank charged, spare battery ready
- [ ] Hotspot on, laptop + Pi + phone connected; dashboard logged in on the phone
- [ ] `turn_90_ms` re-calibrated on the demo floor today
- [ ] Demo reel on the second screen; screen at camera height, bright, no reflections
- [ ] Backup: `python run.py --source data/demo/demo_reel.mp4 --set loitering.seconds=10` works, plus a screen recording of a successful full run
