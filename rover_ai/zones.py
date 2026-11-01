"""Restricted / loiter zones, stored as normalised polygons so they survive resolution changes."""
import json
from pathlib import Path

import cv2
import numpy as np

from .geometry import point_in_polygon

KINDS = ("restricted", "loiter")


class Zones:
    def __init__(self, path: Path):
        self.path = path
        self.data = {k: [] for k in KINDS}
        if path.exists():
            loaded = json.loads(path.read_text())
            for k in KINDS:
                self.data[k] = loaded.get(k, [])

    def has(self, kind):
        return bool(self.data[kind])

    def polygons(self, kind, shape):
        h, w = shape[:2]
        return [np.array([[x * w, y * h] for x, y in poly], dtype=np.int32) for poly in self.data[kind]]

    def contains(self, kind, pt, shape):
        return any(point_in_polygon(pt, p) for p in self.polygons(kind, shape))

    def add(self, kind, norm_points):
        self.data[kind].append(norm_points)
        self.save()

    def clear(self):
        self.data = {k: [] for k in KINDS}
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2))


class ZoneEditor:
    """Click points on the video to draw a polygon; ENTER saves, ESC cancels."""

    def __init__(self, zones: Zones):
        self.zones = zones
        self.kind = None
        self.points = []
        self.shape = None

    @property
    def active(self):
        return self.kind is not None

    def start(self, kind):
        self.kind, self.points = kind, []

    def cancel(self):
        self.kind, self.points = None, []

    def finish(self):
        if self.kind and len(self.points) >= 3 and self.shape is not None:
            h, w = self.shape[:2]
            self.zones.add(self.kind, [[x / w, y / h] for x, y in self.points])
        self.cancel()

    def on_mouse(self, event, x, y, flags, param):
        if self.active and event == cv2.EVENT_LBUTTONDOWN:
            self.points.append((x, y))
