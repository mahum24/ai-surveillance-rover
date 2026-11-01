"""Per-person history across frames (keypoints over time), keyed by tracker ID."""
from collections import deque

import numpy as np

from .geometry import ARM_JOINTS, KP_CONF, body_center, body_scale

MAX_STEP_SPEED = 20.0   # caps keypoint glitches (e.g. left/right swaps)


class PersonTrack:
    def __init__(self, tid, t):
        self.id = tid
        self.first_seen = t
        self.last_seen = t
        self.hist = deque(maxlen=90)      # (t, kpts, box, scale, center, limb_speed)
        # state used by the rules
        self.loiter_since = None
        self.loiter_alerted = False
        self.dwell = 0.0
        self.in_restricted = False

    def add(self, t, person):
        k, b = person.kpts, person.box
        scale, center = body_scale(k, b), body_center(k, b)
        speed = None
        if self.hist:
            t0, k0, _, s0, c0, _ = self.hist[-1]
            speed = _limb_speed(k0, c0, s0, k, center, scale, t - t0)
        self.hist.append((t, k, b, scale, center, speed))
        self.last_seen = t

    @property
    def kpts(self):
        return self.hist[-1][1]

    @property
    def box(self):
        return self.hist[-1][2]

    @property
    def scale(self):
        return self.hist[-1][3]

    @property
    def center(self):
        return self.hist[-1][4]

    def motion_energy(self, window):
        """Average arm speed relative to the body, in body-widths per second.

        Walking moves the whole body (subtracted out); punching and grappling move
        the arms fast relative to the torso, which is what this measures.
        """
        speeds = [h[5] for h in self.hist if h[0] >= self.last_seen - window and h[5] is not None]
        return float(np.mean(speeds)) if speeds else 0.0


def _limb_speed(k0, c0, s0, k1, c1, s1, dt):
    if dt <= 0:
        return None
    vis = (k0[ARM_JOINTS, 2] > KP_CONF) & (k1[ARM_JOINTS, 2] > KP_CONF)
    if not vis.any():
        return 0.0
    rel = (k1[ARM_JOINTS, :2] - c1) - (k0[ARM_JOINTS, :2] - c0)
    v = np.linalg.norm(rel, axis=1)[vis] / ((s0 + s1) / 2) / dt
    return min(float(v.max()), MAX_STEP_SPEED)


class PeopleState:
    def __init__(self, forget_after=2.0):
        self.tracks = {}
        self.forget_after = forget_after

    def update(self, persons, t):
        for p in persons:
            if p.track_id < 0:
                continue
            tr = self.tracks.get(p.track_id)
            if tr is None:
                tr = self.tracks[p.track_id] = PersonTrack(p.track_id, t)
            tr.add(t, p)
        for tid in [k for k, tr in self.tracks.items() if t - tr.last_seen > self.forget_after]:
            del self.tracks[tid]

    def visible(self, t):
        """Tracks seen in the current frame."""
        return [tr for tr in self.tracks.values() if tr.last_seen == t]
