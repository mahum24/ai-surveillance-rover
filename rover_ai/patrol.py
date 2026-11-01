"""Rover behaviour, run on the laptop in a background thread.

Modes
  manual  - the rover only moves when you drive it (keys W/A/S/D or the dashboard)
  guard   - the rover stays parked and watches one scene; all detections, incl. restricted zones
  patrol  - stop-and-scan patrol (the default: the rover wanders by itself when it starts):
              drive forward in short legs, then stop and look in 4 directions. When a person is
              seen it stops and watches them ("investigate"), which is what lets loitering,
              fighting and stealing be detected. On an alert it holds position so the camera
              keeps recording the scene.

Something in front: scene or obstacle?
  The Arduino always refuses to drive forward into something closer than 25 cm. Before the rover
  turns away, it first LOOKS for a moment: if people are in view (or an alert is active), the thing
  in front is part of a scene worth recording, so it stays and watches. Only when nothing of
  interest is in view is it treated as a blocked path: the sonar sweeps and the rover turns toward
  the clearer side. When a scene is over and the path is still blocked, it turns then.
"""
import threading
import time

MODES = ("manual", "guard", "patrol")


class PatrolController:
    def __init__(self, client, cfg):
        self.client = client
        self.c = cfg
        self.mode = cfg["start_mode"] if cfg["start_mode"] in MODES else "manual"
        self.state = "starting"
        self.status = {}
        self.people_seen_at = -1e9
        self.alert_at = -1e9
        self.moving_until = 0.0
        self.watch_since = None             # when the current "watching people" started
        self.ignore_people_until = 0.0      # after watching for investigate_max_s, move on for a while
        self.ready = False                  # becomes True once the detector has processed a frame
        self._mode_changed = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    # ---------------- called from the detection loop / UI ----------------
    def set_mode(self, mode):
        if mode not in MODES or mode == self.mode:
            return
        self.mode = mode
        self._mode_changed.set()
        self.client.stop()
        print(f"[rover] mode -> {mode}")

    def observe(self, people_count):
        self.ready = True
        if people_count:
            self.people_seen_at = time.time()

    def on_alert(self):
        self.alert_at = time.time()
        if self.mode == "patrol":
            self.client.stop()

    def manual_drive(self, cmd):
        """Keyboard / dashboard driving. Switches to manual so patrol doesn't fight the user."""
        if cmd == "stop":
            self.client.stop()
            return
        if self.mode != "manual":
            self.set_mode("manual")
        ms = self.c["turn_90_ms"] // 2 if cmd in ("left", "right") else 400
        self._drive(cmd, ms)

    def camera_moving(self):
        return time.time() < self.moving_until

    # ---------------- internals ----------------
    def _drive(self, cmd, ms):
        self.client.drive(cmd, self.c["speed"], ms)
        self.moving_until = time.time() + ms / 1000 + 0.6   # + time for the picture to settle

    def _people_since(self, since):
        """People seen after `since` (unless we decided to move on from them)."""
        return self.people_seen_at > since and time.time() >= self.ignore_people_until

    def scene_in_view(self, since):
        """People seen after `since`, or an alert still being recorded."""
        return self._people_since(since) or time.time() - self.alert_at < self.c["alert_hold_s"]

    def _wait(self, seconds, stop_on_people=False):
        """Sleep, but return early with a reason: 'mode', 'alert' or 'people'."""
        start = time.time()
        while time.time() - start < seconds:
            if self.mode != "patrol":
                return "mode"
            if self.alert_at > start:
                return "alert"
            if stop_on_people and self._people_since(start):
                return "people"
            time.sleep(0.1)
        return None

    def _run(self):
        while True:
            st = self.client.status()
            if st is not None:
                self.status = st
            if not self.client.online:
                self.state = "rover offline"
                time.sleep(1.0)
                continue
            if not self.ready:
                self.state = "waiting for camera + AI"
                time.sleep(0.3)
                continue
            if self.mode != "patrol":
                self.state = "parked" if self.mode == "guard" else "waiting for commands"
                self._mode_changed.wait(0.5)
                self._mode_changed.clear()
                continue
            try:
                self._patrol_cycle()
            except Exception as e:   # never let a glitch kill the patrol thread
                print(f"[rover] patrol error: {e}")
                self.client.stop()
                time.sleep(1.0)

    def _hold_if_needed(self):
        """Stay still after an alert, or while people are around. Returns True if it held."""
        c = self.c
        held = False
        while self.mode == "patrol" and time.time() - self.alert_at < c["alert_hold_s"]:
            self.state = "alert: holding position"
            held = True
            time.sleep(0.2)
        if held:
            self.watch_since = None         # something happened here: watch the scene for a full period again
        while self.mode == "patrol" and self._people_since(time.time() - c["lost_s"]):
            if self.watch_since is None:
                self.watch_since = time.time()
            if time.time() - self.watch_since >= c["investigate_max_s"]:
                # watched long enough without an alert: move on, and ignore people for a while so it really leaves
                self.ignore_people_until = time.time() + c.get("move_on_s", 20)
                break
            self.state = "watching people"
            held = True
            if time.time() - self.alert_at < c["alert_hold_s"]:
                return self._hold_if_needed()
            time.sleep(0.2)
        if not self._people_since(time.time() - c["lost_s"]):
            self.watch_since = None
        return held

    def _patrol_cycle(self):
        c = self.c
        for _ in range(c["legs_per_scan"]):
            if self._hold_if_needed():
                return
            self.state = "moving forward"
            self._drive("forward", c["leg_ms"])
            reason = self._wait(c["leg_ms"] / 1000 + 0.3, stop_on_people=True)
            if reason:
                self.client.stop()
                return
            st = self.client.status() or {}
            if st.get("obstacle"):
                if self._look_before_turning():
                    return               # a scene is in front: end this cycle; the next one watches it
                self._avoid()
        for _ in range(4):
            self.state = "scanning"
            if self._wait(c["scan_hold_s"], stop_on_people=True):
                return
            self.state = "turning"
            self._drive("right", c["turn_90_ms"])
            if self._wait(c["turn_90_ms"] / 1000 + 0.4) in ("mode", "alert"):
                return

    def _look_before_turning(self):
        """Stopped by something in front: watch for a moment before turning away.
        Returns True if a scene is in view (stay and record), False if the path is just blocked."""
        start = time.time()
        self.state = "something in front: looking"
        self.client.stop()
        reason = self._wait(self.c.get("look_before_turn_s", 1.5), stop_on_people=True)
        if reason in ("people", "alert") or self.scene_in_view(start - self.c["lost_s"]):
            self.state = "something in front: recording the scene"
            return True          # the next cycle watches; if the path is still blocked afterwards, it turns then
        return reason == "mode"

    def _avoid(self):
        c = self.c
        self.state = "obstacle: looking for a way"
        scan = self.client.scan() or {}
        far = 400
        left = scan.get("left") or far
        right = scan.get("right") or far
        if max(left, right) < c["avoid_min_cm"]:
            self.state = "obstacle: backing up"
            self._drive("back", 600)
            self._wait(1.0)
            self._drive("right", 2 * c["turn_90_ms"])
            self._wait(2 * c["turn_90_ms"] / 1000 + 0.4)
        else:
            side = "left" if left > right else "right"
            self.state = f"obstacle: turning {side}"
            self._drive(side, c["turn_90_ms"])
            self._wait(c["turn_90_ms"] / 1000 + 0.4)
