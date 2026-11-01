"""Alert handling: cooldown, snapshot + pre-event clip, JSON log, sound, optional backend POST."""
import json
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2

SOUND = "/System/Library/Sounds/Sosumi.aiff"


class AlertManager:
    def __init__(self, cfg, root: Path):
        self.dir = root / cfg["dir"]
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = self.dir / "alerts.jsonl"
        self.cooldown = cfg["cooldown_seconds"]
        self.clip_seconds = cfg["clip_seconds"]
        self.sound = cfg["sound"] and sys.platform == "darwin"
        self.backend_url = cfg.get("backend_url")
        self.last = {}
        self.recent = deque(maxlen=5)       # (wall time, message) for the on-screen banner
        self.buffer = deque(maxlen=int(self.clip_seconds * 30) + 1)

    def push_frame(self, frame, t):
        self.buffer.append((t, frame))

    def handle(self, event, snapshot, t, save_clip=True):
        now = time.time()
        if now - self.last.get(event.key, -1e9) < self.cooldown:
            return False
        self.last[event.key] = now
        self.recent.append((now, event.message))

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        day = self.dir / stamp[:8]
        day.mkdir(exist_ok=True)
        base = day / f"{stamp}_{event.key}"
        cv2.imwrite(f"{base}.jpg", snapshot)
        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "type": event.kind,
            "message": event.message,
            "track_ids": event.track_ids,
            "snapshot": str(Path(f"{base}.jpg").relative_to(self.dir.parent)),
        }
        if save_clip and len(self.buffer) > 1:
            frames = [(ft, f) for ft, f in self.buffer if ft >= t - self.clip_seconds]
            record["clip"] = str(Path(f"{base}.mp4").relative_to(self.dir.parent))
            threading.Thread(target=_write_clip, args=(f"{base}.mp4", frames), daemon=True).start()
        with self.log.open("a") as f:
            f.write(json.dumps(record) + "\n")

        print(f"[ALERT] {record['time']}  {event.message}")
        if self.sound:
            subprocess.Popen(["afplay", SOUND], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if self.backend_url:
            threading.Thread(target=_post, args=(self.backend_url, record), daemon=True).start()
        return True

    def banner(self, seconds=6.0):
        now = time.time()
        return [m for ts, m in self.recent if now - ts < seconds]


def _write_clip(path, frames):
    if len(frames) < 2:
        return
    span = frames[-1][0] - frames[0][0]
    fps = max(1.0, (len(frames) - 1) / span) if span > 0 else 10.0
    h, w = frames[0][1].shape[:2]
    out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for _, f in frames:
        out.write(f)
    out.release()


def _post(url, record):
    try:
        import requests
        requests.post(url, json=record, timeout=3)
    except Exception as e:  # backend being down must never stop detection
        print(f"[backend] could not send alert: {e}")
