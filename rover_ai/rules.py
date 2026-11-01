"""Rule-based detectors: loitering and unauthorised access. No training needed."""
from .events import Event
from .geometry import anchor_point


class LoiterRule:
    def __init__(self, cfg, anchor_mode):
        self.seconds = cfg["seconds"]
        self.anchor_mode = anchor_mode

    def update(self, tracks, zones, t, shape, flags):
        events = []
        use_zone = zones.has("loiter")
        for tr in tracks:
            inside = zones.contains("loiter", anchor_point(tr.kpts, tr.box, self.anchor_mode), shape) if use_zone else True
            if not inside:
                tr.loiter_since, tr.loiter_alerted, tr.dwell = None, False, 0.0
                continue
            if tr.loiter_since is None:
                tr.loiter_since = t
            tr.dwell = t - tr.loiter_since
            if tr.dwell >= self.seconds:
                flags[tr.id].add("LOITERING")
                if not tr.loiter_alerted:
                    tr.loiter_alerted = True
                    where = "in loiter zone" if use_zone else "in view"
                    events.append(Event("loitering", f"loiter-{tr.id}",
                                        f"LOITERING: ID {tr.id} {where} for {tr.dwell:.0f}s", [tr.id]))
        return events


class AccessRule:
    def __init__(self, anchor_mode):
        self.anchor_mode = anchor_mode

    def update(self, tracks, zones, t, shape, flags):
        events = []
        if not zones.has("restricted"):
            return events
        for tr in tracks:
            inside = zones.contains("restricted", anchor_point(tr.kpts, tr.box, self.anchor_mode), shape)
            if inside:
                flags[tr.id].add("RESTRICTED")
                if not tr.in_restricted:
                    events.append(Event("restricted", f"restricted-{tr.id}",
                                        f"UNAUTHORISED ACCESS: ID {tr.id} entered restricted zone", [tr.id]))
            tr.in_restricted = inside
        return events


def static_access(persons, zones, shape, anchor_mode, flags):
    """Single-image version: is anyone standing in a restricted zone?"""
    events = []
    for i, p in enumerate(persons):
        if zones.has("restricted") and zones.contains("restricted", anchor_point(p.kpts, p.box, anchor_mode), shape):
            flags[p.track_id].add("RESTRICTED")
            events.append(Event("restricted", f"restricted-img-{i}", "UNAUTHORISED ACCESS: person in restricted zone"))
    return events
