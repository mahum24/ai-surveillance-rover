"""Stage 1: person + pose detection with tracking, plus object detection for theft."""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from ultralytics import YOLO


@dataclass
class Person:
    track_id: int          # negative = not (yet) tracked
    box: np.ndarray        # x1, y1, x2, y2
    conf: float
    kpts: np.ndarray       # (17, 3): x, y, confidence


@dataclass
class Item:
    track_id: int
    box: np.ndarray
    conf: float
    name: str


class Detector:
    def __init__(self, cfg, root: Path):
        m = cfg["models"]
        self.pose = YOLO(str(root / m["pose"]))
        self.imgsz = m["imgsz"]
        self.conf = m["conf"]
        self.tracker = m["tracker"]
        self.objects = None
        if cfg["theft"]["enabled"]:
            self.objects = YOLO(str(root / m["objects"]))
            name_to_id = {v: k for k, v in self.objects.names.items()}
            self.object_classes = [name_to_id[n] for n in cfg["theft"]["classes"] if n in name_to_id]
            self.object_conf = m["object_conf"]
            self.object_every = max(1, m["object_every_n_frames"])
        self._frame = 0
        self._items = []

    def __call__(self, frame, track=True):
        """Returns (persons, items, items_fresh). items_fresh is False when objects were reused from an earlier frame."""
        kw = dict(imgsz=self.imgsz, conf=self.conf, verbose=False)
        if track:
            r = self.pose.track(frame, persist=True, tracker=self.tracker, **kw)[0]
        else:
            r = self.pose.predict(frame, **kw)[0]
        persons = []
        if r.boxes is not None and len(r.boxes):
            boxes = r.boxes.xyxy.cpu().numpy()
            confs = r.boxes.conf.cpu().numpy()
            kpts = r.keypoints.data.cpu().numpy()
            ids = r.boxes.id.int().cpu().numpy() if r.boxes.id is not None else -1 - np.arange(len(boxes))
            persons = [Person(int(i), b, float(c), k) for i, b, c, k in zip(ids, boxes, confs, kpts)]

        fresh = False
        if self.objects is not None and (not track or self._frame % self.object_every == 0):
            okw = dict(imgsz=self.imgsz, conf=self.object_conf, classes=self.object_classes, verbose=False)
            if track:
                o = self.objects.track(frame, persist=True, tracker=self.tracker, **okw)[0]
            else:
                o = self.objects.predict(frame, **okw)[0]
            self._items = []
            if o.boxes is not None and len(o.boxes):
                boxes = o.boxes.xyxy.cpu().numpy()
                confs = o.boxes.conf.cpu().numpy()
                cls = o.boxes.cls.int().cpu().numpy()
                ids = o.boxes.id.int().cpu().numpy() if o.boxes.id is not None else -1 - np.arange(len(boxes))
                self._items = [Item(int(i), b, float(c), self.objects.names[int(k)])
                               for i, b, c, k in zip(ids, boxes, confs, cls)]
            fresh = True
        self._frame += 1
        return persons, self._items, fresh
