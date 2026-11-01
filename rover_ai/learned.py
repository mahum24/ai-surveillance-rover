"""Trained fight model: a small GRU that reads the skeletons of two nearby people over 2 seconds.

The same feature code is used for training (training/train_fight.py) and live detection, so
the model sees exactly the same kind of input in both places.

Input window : T frames at 10 Hz of two people (17 COCO keypoints each, plus their boxes)
Features     : body-relative keypoint positions and velocities, normalised by body width,
               plus the distance between the two people and how fast it changes
Output       : probability that the pair is fighting
"""
import json
from collections import deque
from itertools import combinations
from pathlib import Path

import numpy as np
import torch
from torch import nn

from .events import Event
from .geometry import KP_CONF, body_center, body_scale, box_iou

FPS = 10.0                      # the model works on 10 frames per second
FLIP_IDX = [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15]   # left <-> right keypoints
N_FEAT = 2 * (17 * 3 + 17 * 2) + 4


def _person_frames(kpts, boxes):
    """Per-frame body centre (T,2) and one body width for the whole window."""
    centers = np.stack([body_center(k, b) for k, b in zip(kpts, boxes)])
    scale = float(np.median([body_scale(k, b) for k, b in zip(kpts, boxes)]))
    return centers, scale


def window_features(ka, ba, kb, bb):
    """ka, kb: (T,17,3) keypoints; ba, bb: (T,4) boxes of the two people -> (T, N_FEAT) float32."""
    ca, sa = _person_frames(ka, ba)
    cb, sb = _person_frames(kb, bb)
    s = max((sa + sb) / 2, 1.0)
    mid = (ca + cb) / 2                                     # pair centre per frame
    feats = []
    for k, c in ((ka, ca), (kb, cb)):
        vis = (k[:, :, 2] > KP_CONF).astype(np.float32)     # (T,17)
        pos = (k[:, :, :2] - mid[:, None]) / s * vis[..., None]
        own = (k[:, :, :2] - c[:, None]) / s                # relative to the person's own body
        vel = np.zeros_like(own)
        vel[1:] = (own[1:] - own[:-1]) * FPS
        vel[1:] *= (vis[1:] * vis[:-1])[..., None]
        feats += [pos.reshape(len(k), -1), vis, vel.reshape(len(k), -1)]
    d = (cb - ca) / s
    dist = np.linalg.norm(d, axis=1, keepdims=True)
    ddist = np.zeros_like(dist)
    ddist[1:] = (dist[1:] - dist[:-1]) * FPS
    feats += [d, dist, ddist]
    return np.clip(np.concatenate(feats, axis=1), -10, 10).astype(np.float32)


def flip_kpts(k, width):
    """Mirror keypoints horizontally (training augmentation)."""
    k = k[:, FLIP_IDX].copy()
    k[..., 0] = width - k[..., 0]
    return k


def flip_boxes(b, width):
    b = b.copy()
    b[:, [0, 2]] = width - b[:, [2, 0]]
    return b


class FightGRU(nn.Module):
    def __init__(self, n_feat=N_FEAT, hidden=64, layers=2, dropout=0.3):
        super().__init__()
        self.inp = nn.Sequential(nn.Linear(n_feat, hidden), nn.ReLU(), nn.Dropout(dropout))
        self.gru = nn.GRU(hidden, hidden, num_layers=layers, batch_first=True, dropout=dropout)
        self.head = nn.Sequential(nn.Linear(2 * hidden, hidden), nn.ReLU(), nn.Dropout(dropout), nn.Linear(hidden, 1))

    def forward(self, x):                       # x: (B, T, F)
        h, _ = self.gru(self.inp(x))
        z = torch.cat([h[:, -1], h.mean(1)], dim=1)   # last state + average over time
        return self.head(z).squeeze(1)          # logits


def load_model(path):
    path = Path(path)
    meta = json.loads(path.with_suffix(".json").read_text())
    model = FightGRU(**meta["arch"])
    model.load_state_dict(torch.load(path, map_location="cpu"))
    model.eval()
    return model, meta


def pair_probability(model, ka, ba, kb, bb):
    """Average over both person orders, so the answer doesn't depend on who is 'A'."""
    x = np.stack([window_features(ka, ba, kb, bb), window_features(kb, bb, ka, ba)])
    with torch.no_grad():
        return float(torch.sigmoid(model(torch.from_numpy(x))).mean())


MIN_COVER = 0.5                 # a person must have been tracked for at least half the window (1 s)
PAIR_GAP = 1.0                  # a pair keeps its alert history through detection gaps of up to 1 s


def resample_track(hist, t_end, T):
    """Track history -> (T,17,3) keypoints and (T,4) boxes at 10 Hz ending at t_end (linear interpolation;
    before the track started, its first pose is repeated). None if tracked for less than MIN_COVER of the window."""
    times = np.array([h[0] for h in hist])
    t_query = t_end - (T - 1 - np.arange(T)) / FPS
    if (t_query >= times[0] - 0.05).mean() < MIN_COVER:
        return None
    k = np.stack([h[1] for h in hist])
    b = np.stack([h[2] for h in hist])
    idx = np.clip(np.searchsorted(times, t_query), 1, len(times) - 1) if len(times) > 1 else np.zeros(T, int)
    if len(times) == 1:
        return np.repeat(k, T, 0), np.repeat(b, T, 0)
    t0, t1 = times[idx - 1], times[idx]
    w = np.clip((t_query - t0) / np.maximum(t1 - t0, 1e-6), 0, 1)
    kk = k[idx - 1] * (1 - w)[:, None, None] + k[idx] * w[:, None, None]
    bb = b[idx - 1] * (1 - w)[:, None] + b[idx] * w[:, None]
    return kk, bb


class LearnedFightDetector:
    """Drop-in replacement for the rule-based FightDetector, driven by the trained model."""

    def __init__(self, cfg, root):
        self.model, self.meta = load_model(Path(root) / cfg["model"])
        self.T = self.meta["window"]
        self.thr = cfg.get("model_threshold") or self.meta["threshold"]
        self.proximity = cfg["proximity"]
        self.need = cfg.get("model_consecutive") or self.meta.get("consecutive", 3)
        self.window = cfg["window_seconds"]
        self.pairs = {}                 # pair -> last `need` decisions (above threshold or not)
        self.last = {}                  # pair -> time it was last judged
        self.probs = {}

    def update(self, tracks, t, flags):
        events = []
        energy = {tr.id: tr.motion_energy(self.window) for tr in tracks}
        seen = set()
        for a, b in combinations(sorted(tracks, key=lambda tr: tr.id), 2):
            key = (a.id, b.id)
            s = (a.scale + b.scale) / 2
            close = np.linalg.norm(a.center - b.center) < self.proximity * s or box_iou(a.box, b.box) > 0.05
            if not close:
                continue
            wa, wb = resample_track(a.hist, t, self.T), resample_track(b.hist, t, self.T)
            if wa is None or wb is None:
                continue
            seen.add(key)
            p = pair_probability(self.model, *wa, *wb)
            self.probs[key] = p
            if t - self.last.get(key, t) > PAIR_GAP:      # lost for too long: start counting again
                self.pairs.pop(key, None)
            self.last[key] = t
            hist = self.pairs.setdefault(key, deque(maxlen=self.need))
            hist.append(p >= self.thr)
            if len(hist) == self.need and all(hist):
                flags[a.id].add("FIGHT")
                flags[b.id].add("FIGHT")
                events.append(Event("fight", f"fight-{a.id}-{b.id}",
                                    f"FIGHT: ID {a.id} and ID {b.id} ({p:.0%})", [a.id, b.id]))
        for key in [k for k in self.probs if k not in seen]:
            self.probs.pop(key, None)
        for key in [k for k, last in self.last.items() if t - last > PAIR_GAP]:
            self.pairs.pop(key, None)                    # keep a pair's history through short detection gaps
            self.last.pop(key, None)
        return events, energy
