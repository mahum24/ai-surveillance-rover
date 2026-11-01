"""Theft / stealing detection from object + hand interaction.

An object that stays still for `guard_seconds` becomes "guarded". If a person's
hand touches a guarded object and the object then moves away or disappears,
that person is flagged for taking it.
"""
import numpy as np

from .events import Event
from .geometry import WRISTS, box_center, expand_box, point_in_box, visible


class ObjectState:
    def __init__(self, item, t):
        self.name = item.name
        self.box = item.box
        self.anchor = box_center(item.box)
        self.still_since = t
        self.last_seen = t
        self.guarded = False
        self.toucher = None          # last person whose hand touched it
        self.touched_at = -1e9

    @property
    def size(self):
        return max(self.box[2] - self.box[0], self.box[3] - self.box[1], 1.0)


class TheftDetector:
    def __init__(self, cfg):
        self.guard_seconds = cfg["guard_seconds"]
        self.move_fraction = cfg["move_fraction"]
        self.vanish_seconds = cfg["vanish_seconds"]
        self.hand_memory = cfg["hand_memory_seconds"]
        self.objects = {}

    def reset(self):
        """Forget all objects (called while the camera is moving)."""
        self.objects.clear()

    def update(self, items, tracks, t, fresh, flags):
        events = []
        if fresh:
            for it in items:
                if it.track_id < 0:
                    continue
                ob = self.objects.get(it.track_id)
                if ob is None:
                    self.objects[it.track_id] = ObjectState(it, t)
                    continue
                ob.box, ob.last_seen = it.box, t
                moved = np.linalg.norm(box_center(it.box) - ob.anchor)
                if not ob.guarded:
                    if moved > 0.3 * ob.size:          # still settling: reset the stillness timer
                        ob.anchor, ob.still_since = box_center(it.box), t
                    elif t - ob.still_since >= self.guard_seconds:
                        ob.guarded = True
                elif moved > self.move_fraction * ob.size and t - ob.touched_at <= self.hand_memory:
                    events.append(self._taken(it.track_id, ob, "picked up"))
                    ob.guarded, ob.anchor, ob.still_since = False, box_center(it.box), t

        # who is touching what (checked every frame, using the latest object boxes)
        for ob in self.objects.values():
            zone = expand_box(ob.box, 0.25)
            for tr in tracks:
                if any(visible(tr.kpts, w) and point_in_box(tr.kpts[w, :2], zone) for w in WRISTS):
                    ob.toucher, ob.touched_at = tr.id, t

        for oid in list(self.objects):
            ob = self.objects[oid]
            gone = t - ob.last_seen
            if ob.guarded and gone >= self.vanish_seconds and ob.touched_at >= ob.last_seen - self.hand_memory:
                events.append(self._taken(oid, ob, "took away"))
                del self.objects[oid]
            elif gone > max(self.vanish_seconds, 3.0):
                del self.objects[oid]

        for e in events:
            for tid in e.track_ids:
                flags[tid].add("THEFT")
        return events

    def _taken(self, oid, ob, how):
        who = f"ID {ob.toucher}" if ob.toucher is not None else "someone"
        return Event("theft", f"theft-{oid}", f"STEALING: {who} {how} the {ob.name}",
                     [ob.toucher] if ob.toucher is not None else [])

    def status(self, item):
        ob = self.objects.get(item.track_id)
        return "guarded" if ob is not None and ob.guarded else ""
