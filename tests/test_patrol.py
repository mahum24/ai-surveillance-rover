"""Checks for the patrol behaviour (rover_ai/patrol.py) with a fake rover. No hardware needed.

    python tests/test_patrol.py
"""
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rover_ai.patrol import PatrolController  # noqa: E402

CFG = dict(start_mode="patrol", speed=150, leg_ms=50, legs_per_scan=1, turn_90_ms=50, scan_hold_s=0.05,
           lost_s=0.3, investigate_max_s=0.6, move_on_s=2.0, alert_hold_s=0.5, avoid_min_cm=40, look_before_turn_s=0.6)


class FakeRover:
    """Records every command; reports an obstacle in front when told to."""
    online = False                      # keeps the controller's own background loop idle; the test drives it

    def __init__(self, obstacle=False):
        self.obstacle = obstacle
        self.commands = []
        self.times = {}                 # first time each command was sent

    def status(self):
        return {"obstacle": self.obstacle}

    def drive(self, cmd, speed=150, ms=500):
        self.commands.append(cmd)
        self.times.setdefault(cmd, time.time())

    def stop(self):
        self.commands.append("stop")

    def scan(self):
        return {"left": 120, "right": 30}   # more room on the left


def check(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  ({detail})" if detail else ""))
    return ok


def turns(commands):
    return [c for c in commands if c in ("left", "right")]


def main():
    results = []

    # 1. something in front and nobody in view: it is just a blocked path -> turn toward the clearer side
    rover = FakeRover(obstacle=True)
    ctl = PatrolController(rover, CFG)
    ctl.ready = True
    ctl._patrol_cycle()
    first_turn = turns(rover.commands)[:1]
    results.append(check("blocked path, nobody in view: turns toward the clearer side (left)",
                         first_turn == ["left"], f"commands {rover.commands[:6]}"))

    # 2. something in front, and a person shows up while it looks: a scene to record -> stay, no turning
    rover = FakeRover(obstacle=True)
    ctl = PatrolController(rover, CFG)
    ctl.ready = True
    threading.Timer(0.6, ctl.observe, args=(1,)).start()     # person appears during the look
    ctl._patrol_cycle()
    results.append(check("something in front with a person in view: stays and records, no turn",
                         not turns(rover.commands) and rover.commands[-1] == "stop", f"commands {rover.commands}"))

    # 3. people stay in view for a long time without an alert: after investigate_max_s it moves on
    rover = FakeRover(obstacle=False)
    ctl = PatrolController(rover, CFG)
    ctl.ready = True
    stop = threading.Event()

    def people_always():
        while not stop.is_set():
            ctl.observe(1)
            time.sleep(0.05)
    threading.Thread(target=people_always, daemon=True).start()
    t0 = time.time()
    while "forward" not in rover.commands and time.time() - t0 < 5:
        ctl._patrol_cycle()
    waited = rover.times.get("forward", time.time()) - t0
    stop.set()
    results.append(check("watches people, then moves on after investigate_max_s",
                         "forward" in rover.commands and CFG["investigate_max_s"] - 0.1 <= waited < 2.5,
                         f"moved after {waited:.1f}s"))

    # 4. an alert stops the rover and it holds position while recording
    rover = FakeRover(obstacle=False)
    ctl = PatrolController(rover, CFG)
    ctl.ready = True
    ctl.on_alert()
    t0 = time.time()
    held = ctl._hold_if_needed()
    results.append(check("alert: stops and holds position for alert_hold_s",
                         held and rover.commands[:1] == ["stop"] and time.time() - t0 >= CFG["alert_hold_s"] - 0.1))

    # 5. an alert restarts the watching period: the rover doesn't leave right after something happened
    rover = FakeRover(obstacle=False)
    ctl = PatrolController(rover, CFG)
    ctl.ready = True
    stop = threading.Event()
    threading.Thread(target=lambda: [ctl.observe(1) or time.sleep(0.05) for _ in iter(stop.is_set, True)],
                     daemon=True).start()
    threading.Timer(0.4, ctl.on_alert).start()             # alert 0.4 s into watching
    t0 = time.time()
    while "forward" not in rover.commands and time.time() - t0 < 5:
        ctl._patrol_cycle()
    stop.set()
    moved = rover.times.get("forward", time.time()) - t0
    expect = 0.4 + CFG["alert_hold_s"] + CFG["investigate_max_s"]
    results.append(check("an alert restarts the watching period", abs(moved - expect) < 1.0,
                         f"moved after {moved:.1f}s, expected ~{expect:.1f}s"))

    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
