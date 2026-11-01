"""On-screen overlay: zones, skeletons, IDs, object states, alert banner, help bar."""
import cv2
import numpy as np

from .geometry import KP_CONF, SKELETON

GREEN = (80, 200, 80)
ORANGE = (0, 165, 255)
RED = (0, 0, 255)
YELLOW = (0, 220, 255)
CYAN = (255, 200, 0)
GREY = (170, 170, 170)
WHITE = (255, 255, 255)
FONT = cv2.FONT_HERSHEY_SIMPLEX

ZONE_COLORS = {"restricted": RED, "loiter": YELLOW}


def _text(img, txt, org, color=WHITE, scale=0.5, bg=(0, 0, 0)):
    (w, h), base = cv2.getTextSize(txt, FONT, scale, 1)
    x, y = int(org[0]), int(org[1])
    cv2.rectangle(img, (x, y - h - base - 2), (x + w + 4, y + 2), bg, -1)
    cv2.putText(img, txt, (x + 2, y - base + 1), FONT, scale, color, 1, cv2.LINE_AA)


def person_color(flags):
    if flags & {"FIGHT", "FIGHT?", "RESTRICTED", "THEFT"}:
        return RED
    if "LOITERING" in flags:
        return ORANGE
    return GREEN


def draw_zones(img, zones, editor):
    overlay = img.copy()
    for kind, color in ZONE_COLORS.items():
        for poly in zones.polygons(kind, img.shape):
            cv2.fillPoly(overlay, [poly], color)
    cv2.addWeighted(overlay, 0.2, img, 0.8, 0, img)
    for kind, color in ZONE_COLORS.items():
        for poly in zones.polygons(kind, img.shape):
            cv2.polylines(img, [poly], True, color, 2)
            _text(img, kind.upper(), poly[0], color)
    if editor is not None and editor.active:
        pts = np.array(editor.points, dtype=np.int32)
        color = ZONE_COLORS[editor.kind]
        for p in pts:
            cv2.circle(img, tuple(int(v) for v in p), 4, color, -1)
        if len(pts) > 1:
            cv2.polylines(img, [pts], False, color, 2)


def draw_person(img, kpts, box, label, color):
    for a, b in SKELETON:
        if kpts[a, 2] > KP_CONF and kpts[b, 2] > KP_CONF:
            cv2.line(img, tuple(kpts[a, :2].astype(int)), tuple(kpts[b, :2].astype(int)), color, 2, cv2.LINE_AA)
    for x, y, c in kpts:
        if c > KP_CONF:
            cv2.circle(img, (int(x), int(y)), 3, color, -1)
    x1, y1, x2, y2 = box.astype(int)
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    _text(img, label, (x1, max(y1, 18)), WHITE, bg=color)


def draw_item(img, item, status):
    color = CYAN if status else GREY
    x1, y1, x2, y2 = item.box.astype(int)
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    label = item.name + (f" [{status}]" if status else "")
    _text(img, label, (x1, y2 + 16), (0, 0, 0), bg=color)


def draw_hud(img, banner, fps, editor=None, paused=False):
    h, w = img.shape[:2]
    for i, msg in enumerate(banner):
        y = 30 + i * 30
        cv2.rectangle(img, (0, y - 24), (w, y + 6), RED, -1)
        cv2.putText(img, msg, (10, y), FONT, 0.7, WHITE, 2, cv2.LINE_AA)
    if editor is not None and editor.active:
        help_txt = f"Drawing {editor.kind.upper()} zone: click points, ENTER = save, ESC = cancel"
    else:
        help_txt = "[Z] restricted zone  [L] loiter zone  [C] clear zones  [D] debug  [P] pause  [Q] quit"
    status = f"FPS {fps:4.1f}" if fps is not None else ""
    if paused:
        status += "  PAUSED"
    cv2.rectangle(img, (0, h - 26), (w, h), (0, 0, 0), -1)
    cv2.putText(img, f"{status}   {help_txt}", (8, h - 8), FONT, 0.5, WHITE, 1, cv2.LINE_AA)
