# Mobile app plan (Android)

**Status:** planned, not built yet. This page is the complete design, so it can be coded later without new decisions. Until then, the phone uses the dashboard in the browser (`http://<laptop-ip>:8080`).

## 1. What the app must do

1. **Control the rover.**
   - See the live annotated camera.
   - Switch **Patrol / Guard / Manual**.
   - Drive with arrow buttons and stop.
   - See the rover's status: mode, what it is doing, people in view, sonar distance, online or offline.
2. **Always show alerts.**
   - Every alert (fight, loitering, restricted zone, stealing) appears as a **phone notification within a few seconds**, with sound, vibration and the snapshot.
   - This works even when the app is closed or the screen is locked.
   - Tapping the notification opens the alert with its snapshot and video clip.
3. **Warn when something is wrong:** "Rover offline", or "Detector stopped".
4. **Label alerts** as *real* or *false alarm*. These labels feed the retraining loop (`TRAINING.md` section 8).

The rover itself already **starts patrolling on its own** when it is switched on (`rover.start_mode: patrol`). The app doesn't need to start it, only to show what it is doing and to change the mode when you want.

## 2. How it fits into the system

```
 Rover (Pi + Arduino) ──Wi-Fi──► Laptop: run.py (AI + patrol) ──► backend (FastAPI, port 8080)
                                          │ live view (port 8081)          ▲   │
                                          ▼                                │   ▼
                                     ┌──────────────── Android app ────────────────┐
                                     │ live view · status · mode/drive · alerts     │
                                     │ background service → notifications           │
                                     └──────────────────────────────────────────────┘
```

- The phone, laptop and rover are on the **same Wi-Fi or hotspot**. **No internet and no Google/Firebase account are needed**, so it works anywhere, including on demo day.
- The app talks only to the **backend**, with the same API the browser dashboard uses. It never talks to the Pi directly, so all security stays in one place.

### API the app uses (already exists in `backend/app.py`)

| Purpose | Request |
|---|---|
| Log in | `POST /api/login` → token |
| Rover and detector status | `GET /api/status` → `detector_online`, `fps`, `people`, `rover.mode`, `rover.state`, `rover.online`, `rover.distance_cm` … |
| Change mode / drive | `POST /api/command` `{"mode": "patrol"}` or `{"drive": "forward"}` |
| New alerts since the last one seen | `GET /api/alerts?after_id=<last id>` |
| Alert counts | `GET /api/alerts/stats` |
| Label an alert | `PATCH /api/alerts/{id}` `{"label": "real"}` |
| Snapshot / clip | `GET /media/<path>?token=…` |
| Live annotated video | `http://<laptop-ip>:8081/` (MJPEG), `/snapshot.jpg` (one picture) |

### Small backend additions (planned with the app)

| Addition | Why |
|---|---|
| `GET /api/alerts/wait?after_id=N` (long poll: answers as soon as a new alert exists, or after 25 s) | Notifications arrive **instantly** and the phone sleeps between alerts (less battery than asking every 2 s) |
| Longer-lived app login (30-day token, or `POST /api/refresh`) | The app login now expires after 12 h; a background app must not silently stop |
| `GET /media/<path>?w=320` (small thumbnail) | Snapshot in the notification without downloading the full image |
| Optional "rover offline" event in the alert stream | The phone is warned even when it isn't polling status |

## 3. Screens

| Screen | Contents |
|---|---|
| **Connect / login** | Laptop address (pre-filled `http://192.168.x.x:8080`, with a "find on this network" button later), username, password, "stay logged in" |
| **Home** | Live view (top half). Status card: mode, state (`patrolling`, `watching people`, `alert: holding position` …), people, sonar, online/offline. Three big mode buttons: **Patrol / Guard / Manual**. Drive pad: ▲ ◀ ▶ ▼ while pressed, big red **STOP**. Last alert banner |
| **Alerts** | List, newest first: icon by type, message, time, thumbnail; filter by type; unread marked |
| **Alert detail** | Snapshot, video clip player, message, time, IDs, buttons **Real** / **False alarm** |
| **Settings** | Notifications on/off per alert type, sound, vibration, "send a test notification", laptop address, log out |

## 4. Always-on alerts: how notifications work

1. After login, the app starts a **foreground service** called "Rover watch". Android requires a small permanent notification ("Watching the rover: connected") for such services. This is what keeps the app alive in the background.
2. The service asks the backend for new alerts:
   - with `GET /api/alerts/wait?after_id=N` once it exists
   - until then, with `GET /api/alerts?after_id=N` every 3 s
3. For every new alert, it posts a **high-priority notification** on an "Alerts" channel:
   - sound, vibration, shown on the lock screen and as a heads-up banner
   - for example **"FIGHT: ID 9 and ID 15 (97%)"**, with the time
   - the snapshot as a big picture
   - tapping it opens *Alert detail*
4. It also checks `/api/status` every 10 s. If the detector or rover has been offline for more than 15 s, it posts **"Rover offline"**, and **"Rover back online"** when it returns.
5. On Wi-Fi loss it retries with increasing waits (1 s → 30 s) and shows "Not connected" in the permanent notification.
6. It remembers the last alert ID, so after a reconnect it shows what was missed, without repeating old alerts.

**Android details to get right:**

| Item | What to do |
|---|---|
| Android 13+ | Ask for the `POST_NOTIFICATIONS` permission on first start |
| Foreground service | `FOREGROUND_SERVICE` + type `specialUse`. The app is installed directly, not from the Play Store. Type `dataSync` is limited to 6 h a day on Android 15, so don't use it |
| Battery saver | Ask the user to exempt the app from battery optimisation; otherwise some phones (Xiaomi, Oppo, Vivo, Samsung) stop it |
| Local HTTP | Allow clear-text HTTP for the local network only (`network_security_config.xml`). The hotspot is private; HTTPS can be added later |
| Start after reboot | Optional: `RECEIVE_BOOT_COMPLETED` → restart the service if the user is logged in |

**Why not Firebase push notifications?** They need internet and a Google project, and the laptop would need to reach Google's servers. The rover system is designed to work on an offline hotspot. Firebase can be added later as an option.

## 5. Technology

| Part | Choice |
|---|---|
| Language / UI | Kotlin + Jetpack Compose (Android Studio) |
| Phones | Android 8.0 and newer (minSdk 26), target the latest SDK |
| Network | OkHttp + Retrofit, kotlinx.serialization |
| Images / video | Coil (snapshots), Media3 ExoPlayer (H.264 clips) |
| Live view | MJPEG read with OkHttp and decoded frame by frame (ExoPlayer can't play MJPEG) |
| Settings / token | DataStore (encrypted) |
| Project folder | `mobile/` in this repository |

## 6. Building and installing (when it is coded)

1. Install **Android Studio** (free; about 4 GB with the SDK).
2. Open the `mobile/` folder and let Gradle sync.
3. On the phone, enable **Developer options → USB debugging** and connect the cable.
4. Press **Run**: the app installs and opens. Or use **Build → Build APK**, copy the `.apk` to the phone and install it (allow "install unknown apps").

## 7. Work plan

| Step | Work | Days |
|---|---|---|
| 1 | Project, login screen, Home with status from `/api/status` | 2 |
| 2 | Mode buttons, drive pad, STOP (`/api/command`), live view | 1–2 |
| 3 | Alerts list and detail with snapshot, clip and labels | 2 |
| 4 | Background service, notifications, offline warning, battery-saver prompt | 2 |
| 5 | Backend: long poll, longer token, thumbnails | 1 |
| 6 | Testing on a real phone (checklist below), fixes | 1–2 |
| | **Total** | **≈ 9–11 days** |

## 8. Test checklist for the app

| # | Test | Pass if |
|---|---|---|
| A1 | Log in with the right and the wrong password | Right: Home opens. Wrong: clear error message |
| A2 | Press Patrol / Guard / Manual | Rover changes mode within 1–2 s; Home shows it |
| A3 | Hold ▲, release; press STOP | Rover moves while held and stops at once |
| A4 | Play the demo reel (`HOW_IT_WORKS_AND_DEMO.md`) with the **app open** | One notification per incident, with snapshot |
| A5 | Same with the **app closed** | Notifications still arrive within 5 s |
| A6 | Same with the **screen locked** for 30 minutes | Notifications still arrive |
| A7 | Stop `run.py` | "Rover offline" within about 20 s; "back online" after restart |
| A8 | Switch the hotspot off and on | App reconnects by itself and shows missed alerts once |
| A9 | Tap a notification | Opens the right alert; clip plays |
| A10 | Mark alerts real / false | Label visible in the browser dashboard too |
