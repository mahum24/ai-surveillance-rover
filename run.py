"""Suspicious-activity detector: webcam, video file, image or folder of images.

    python run.py                      # webcam
    python run.py --source clip.mp4    # video file
    python run.py --source photo.jpg   # single image (or a folder of images)
"""
import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import yaml

from rover_ai.alerts import AlertManager
from rover_ai.draw import draw_hud, draw_item, draw_person, draw_zones, person_color
from rover_ai.fight import FightDetector, static_fight
from rover_ai.rules import AccessRule, LoiterRule, static_access
from rover_ai.theft import TheftDetector
from rover_ai.tracks import PeopleState
from rover_ai.vision import Detector
from rover_ai.zones import ZoneEditor, Zones

ROOT = Path(__file__).resolve().parent
WINDOW = "Surveillance"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="webcam index, video, image or image folder (default: config camera.source)")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--save", help="write the annotated video to this .mp4 path")
    ap.add_argument("--headless", action="store_true", help="no window (for testing / servers)")
    ap.add_argument("--max-frames", type=int, default=0, help="stop after N frames (0 = no limit)")
    ap.add_argument("--debug", action="store_true", help="show per-person motion values (toggle with D)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a config value, e.g. --set loitering.seconds=8 (repeatable)")
    return ap.parse_args()


def apply_overrides(cfg, overrides):
    for item in overrides:
        key, _, value = item.partition("=")
        *parents, leaf = key.split(".")
        node = cfg
        for p in parents:
            node = node[p]
        if leaf not in node:
            sys.exit(f"Unknown config key: {key}")
        node[leaf] = yaml.safe_load(value)


def label_for(tr_id, flags, dwell=None, energy=None, debug=False):
    parts = [f"ID {tr_id}" if tr_id >= 0 else "person"]
    if dwell is not None and dwell >= 1:
        parts.append(f"{dwell:.0f}s")
    parts += sorted(flags)
    if debug and energy is not None:
        parts.append(f"m={energy:.1f}")
    return " | ".join(parts)


def open_capture(source, cam_cfg):
    if str(source).isdigit():
        backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
        cap = cv2.VideoCapture(int(source), backend)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam_cfg["width"])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam_cfg["height"])
        live = True
    else:
        cap = cv2.VideoCapture(str(source))
        live = False
    if not cap.isOpened():
        msg = f"Could not open source: {source}"
        if live and sys.platform == "darwin":
            msg += ("\nOn macOS, allow camera access for your terminal app in "
                    "System Settings > Privacy & Security > Camera, then restart the terminal.")
        sys.exit(msg)
    return cap, live


def run_stream(source, cfg, args):
    cap, live = open_capture(source, cfg["camera"])
    file_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    mirror = live and cfg["camera"]["mirror"]
    anchor = cfg["zones"]["anchor"]

    detector = Detector(cfg, ROOT)
    zones = Zones(ROOT / cfg["zones"]["file"])
    editor = ZoneEditor(zones)
    alerts = AlertManager(cfg["alerts"], ROOT)
    people = PeopleState()
    loiter = LoiterRule(cfg["loitering"], anchor) if cfg["loitering"]["enabled"] else None
    access = AccessRule(anchor) if cfg["access"]["enabled"] else None
    fight = FightDetector(cfg["fight"]) if cfg["fight"]["enabled"] else None
    theft = TheftDetector(cfg["theft"]) if cfg["theft"]["enabled"] else None

    if not args.headless:
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW, editor.on_mouse)

    writer = None
    debug, paused = args.debug, False
    frame_idx, fps, last_tick = 0, None, time.time()
    view = None
    while True:
        if not paused:
            ok, frame = cap.read()
            if not ok:
                break
            if mirror:
                frame = cv2.flip(frame, 1)
            # video files use their own clock so timings are right even if we process slower than real time
            t = time.time() if live else frame_idx / file_fps
            editor.shape = frame.shape
            alerts.push_frame(frame, t)

            persons, items, fresh = detector(frame)
            people.update(persons, t)
            tracks = people.visible(t)
            flags = defaultdict(set)
            events, energy = [], {}
            if loiter:
                events += loiter.update(tracks, zones, t, frame.shape, flags)
            if access:
                events += access.update(tracks, zones, t, frame.shape, flags)
            if fight:
                ev, energy = fight.update(tracks, t, flags)
                events += ev
            if theft:
                events += theft.update(items, tracks, t, fresh, flags)

            view = frame.copy()
            draw_zones(view, zones, editor)
            for it in items:
                draw_item(view, it, theft.status(it) if theft else "")
            for p in persons:
                tr = people.tracks.get(p.track_id)
                f = flags.get(p.track_id, set())
                draw_person(view, p.kpts, p.box,
                            label_for(p.track_id, f, tr.dwell if tr else None, energy.get(p.track_id), debug),
                            person_color(f))
            for e in events:
                alerts.handle(e, view, t)

            now = time.time()
            inst = 1.0 / max(now - last_tick, 1e-6)
            fps = inst if fps is None else 0.9 * fps + 0.1 * inst
            last_tick = now
            frame_idx += 1
        else:
            view = frame.copy()
            draw_zones(view, zones, editor)

        shown = view.copy()
        draw_hud(shown, alerts.banner(), fps, editor, paused)
        if args.save:
            if writer is None:
                h, w = shown.shape[:2]
                writer = cv2.VideoWriter(args.save, cv2.VideoWriter_fourcc(*"mp4v"), file_fps if not live else 10, (w, h))
            writer.write(shown)
        if args.max_frames and frame_idx >= args.max_frames:
            break
        if args.headless:
            continue

        cv2.imshow(WINDOW, shown)
        key = cv2.waitKey(1) & 0xFF
        if editor.active:
            if key in (13, 10):        # Enter
                editor.finish()
            elif key == 27:            # Esc
                editor.cancel()
            continue
        if key in (ord("q"), 27):
            break
        elif key == ord("z"):
            editor.start("restricted")
        elif key == ord("l"):
            editor.start("loiter")
        elif key == ord("c"):
            zones.clear()
        elif key == ord("d"):
            debug = not debug
        elif key == ord("p"):
            paused = not paused

    cap.release()
    if writer:
        writer.release()
    cv2.destroyAllWindows()


def run_images(paths, cfg, args):
    detector = Detector(cfg, ROOT)
    zones = Zones(ROOT / cfg["zones"]["file"])
    alerts = AlertManager(cfg["alerts"], ROOT)
    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    for path in paths:
        frame = cv2.imread(str(path))
        if frame is None:
            print(f"skipping unreadable image: {path}")
            continue
        persons, items, _ = detector(frame, track=False)
        flags = defaultdict(set)
        events = []
        if cfg["fight"]["enabled"]:
            events += static_fight(persons, cfg["fight"]["proximity"], flags)
        if cfg["access"]["enabled"]:
            events += static_access(persons, zones, frame.shape, cfg["zones"]["anchor"], flags)

        view = frame.copy()
        draw_zones(view, zones, None)
        for it in items:
            draw_item(view, it, "")
        for p in persons:
            f = flags.get(p.track_id, set())
            draw_person(view, p.kpts, p.box, label_for(p.track_id, f), person_color(f))
        for e in events:
            alerts.handle(e, view, 0, save_clip=False)
        summary = f"{len(persons)} people, {len(items)} objects" + (f" -> {len(events)} alert(s)" if events else ", nothing suspicious")
        print(f"{path.name}: {summary}")
        out = out_dir / f"{path.stem}_result.jpg"
        cv2.imwrite(str(out), view)

        if not args.headless:
            draw_hud(view, [e.message for e in events], None)
            cv2.imshow(WINDOW, view)
            if (cv2.waitKey(0) & 0xFF) in (ord("q"), 27):
                break
    cv2.destroyAllWindows()
    print(f"Annotated images saved to {out_dir}")


def main():
    args = parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    apply_overrides(cfg, args.set)
    source = args.source if args.source is not None else str(cfg["camera"]["source"])
    p = Path(source)
    if p.is_dir():
        images = sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXT)
        run_images(images, cfg, args)
    elif p.suffix.lower() in IMAGE_EXT:
        run_images([p], cfg, args)
    else:
        run_stream(source, cfg, args)


if __name__ == "__main__":
    main()
