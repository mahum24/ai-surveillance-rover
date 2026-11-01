"""Pose-based fight detection.

Video: two people are close, at least one has fast limb motion relative to their
own body, and a hand is reaching the other person (or both are moving fast).
This must hold for several frames in a short window, which filters out a single
wave or high-five.

Single image: two people close, with an extended arm whose wrist lands on the
other person's upper body. This is weaker evidence, so it's reported as "possible".
"""
from collections import deque
from itertools import combinations

import numpy as np

from .events import Event
from .geometry import ARMS, WRISTS, body_center, body_scale, box_iou, expand_box, joint_angle, point_in_box, visible


def _hand_reaches(kpts_a, box_b):
    """Does a wrist of person A lie inside person B's (slightly enlarged) box?"""
    zone = expand_box(box_b, 0.1)
    return any(visible(kpts_a, w) and point_in_box(kpts_a[w, :2], zone) for w in WRISTS)


def _strike_pose(kpts_a, box_b):
    """Extended arm (elbow nearly straight) with the wrist on B's upper body."""
    upper = box_b.copy()
    upper[3] = box_b[1] + 0.6 * (box_b[3] - box_b[1])
    for s, e, w in ARMS:
        if all(visible(kpts_a, i) for i in (s, e, w)):
            if joint_angle(kpts_a[s, :2], kpts_a[e, :2], kpts_a[w, :2]) > 140 and point_in_box(kpts_a[w, :2], upper):
                return True
    return False


def _close(ka, ba, kb, bb, proximity):
    scale = (body_scale(ka, ba) + body_scale(kb, bb)) / 2
    dist = np.linalg.norm(body_center(ka, ba) - body_center(kb, bb))
    return dist < proximity * scale or box_iou(ba, bb) > 0.05


class FightDetector:
    def __init__(self, cfg):
        self.window = cfg["window_seconds"]
        self.thr = cfg["motion_threshold"]
        self.proximity = cfg["proximity"]
        self.n = cfg["window_frames"]
        self.min_pos = cfg["min_positive_frames"]
        self.pairs = {}

    def update(self, tracks, t, flags):
        events = []
        energy = {tr.id: tr.motion_energy(self.window) for tr in tracks}
        seen = set()
        for a, b in combinations(sorted(tracks, key=lambda tr: tr.id), 2):
            key = (a.id, b.id)
            seen.add(key)
            hist = self.pairs.setdefault(key, deque(maxlen=self.n))
            hist.append(self._positive(a, b, energy))
            if sum(hist) >= self.min_pos:
                flags[a.id].add("FIGHT")
                flags[b.id].add("FIGHT")
                events.append(Event("fight", f"fight-{a.id}-{b.id}",
                                    f"FIGHT: ID {a.id} and ID {b.id}", [a.id, b.id]))
        for key in [k for k in self.pairs if k not in seen]:
            del self.pairs[key]
        return events, energy

    def _positive(self, a, b, energy):
        if not _close(a.kpts, a.box, b.kpts, b.box, self.proximity):
            return False
        hi, lo = max(energy[a.id], energy[b.id]), min(energy[a.id], energy[b.id])
        if hi < self.thr:
            return False
        contact = _hand_reaches(a.kpts, b.box) or _hand_reaches(b.kpts, a.box)
        return contact or lo > 0.5 * self.thr


def static_fight(persons, proximity, flags):
    events = []
    for i, j in combinations(range(len(persons)), 2):
        a, b = persons[i], persons[j]
        if not _close(a.kpts, a.box, b.kpts, b.box, proximity):
            continue
        if _strike_pose(a.kpts, b.box) or _strike_pose(b.kpts, a.box):
            flags[a.track_id].add("FIGHT?")
            flags[b.track_id].add("FIGHT?")
            events.append(Event("fight", f"fight-img-{i}-{j}", "POSSIBLE FIGHT: strike pose between two people"))
    return events
