"""Theft logic check without a camera: simulates a laptop on a table and a hand.

    python tests/test_theft_sim.py
"""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from rover_ai.theft import TheftDetector  # noqa: E402
from rover_ai.tracks import PeopleState  # noqa: E402
from rover_ai.vision import Item, Person  # noqa: E402


def fake_person(x, wrist):
    k = np.zeros((17, 3))
    k[:, 0], k[:, 1], k[:, 2] = x, 200, 0.9
    k[5], k[6] = [x - 20, 150, .9], [x + 20, 150, .9]     # shoulders
    k[11], k[12] = [x - 15, 250, .9], [x + 15, 250, .9]   # hips
    k[10] = [*wrist, .9]                                  # right wrist
    return Person(1, np.array([x - 40, 80, x + 40, 400.]), .9, k)


def simulate(scenario, cfg):
    """10 fps for 8 s. The laptop sits still from t=0; the hand reaches it at t=4.5 s."""
    theft, people, alerts = TheftDetector(cfg), PeopleState(), []
    for i in range(80):
        t = i / 10
        laptop = np.array([300, 300, 380, 350.])
        if scenario == "picked up" and t >= 5:
            lift = 150 * min(1.0, t - 5)
            laptop = laptop + [0, -lift, 0, -lift]
        items = [] if scenario == "vanished" and t >= 5 else [Item(7, laptop, .8, "laptop")]
        touching = scenario != "never touched" and t >= 4.5
        wrist = ((laptop[0] + laptop[2]) / 2, laptop[1]) if touching else (520, 230)
        people.update([fake_person(500, wrist)], t)
        alerts += [f"t={t:.1f}s {e.message}" for e in theft.update(items, people.visible(t), t, True, defaultdict(set))]
    return alerts


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())["theft"]
    expected = {"picked up": True, "vanished": True, "never touched": False}
    ok = True
    for scenario, should_alert in expected.items():
        alerts = simulate(scenario, cfg)
        passed = bool(alerts) == should_alert
        ok &= passed
        print(f"[{'PASS' if passed else 'FAIL'}] laptop {scenario:13} -> {alerts or 'no alert'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
