"""Suspicious-activity detector: webcam, video file, images, or the rover's live camera.

    python run.py                                   # laptop webcam
    python run.py --source clip.mp4                 # video file
    python run.py --source photo.jpg                # single image (or a folder of images)
    python run.py --rover http://<pi-ip>:8000       # the rover: its camera + driving + patrol
"""
import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
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
DRIVE_KEYS = {ord("w"): "forward", ord("s"): "back", ord("a"): "left", ord("d"): "right", ord(" "): "stop"}
MODE_KEYS = {ord("g"): "patrol", ord("h"): "guard", ord("m"): "manual"}


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="webcam index, video, image, image folder or stream URL (default: config)")
    ap.add_argument("--rover", help="rover agent URL, e.g. http://192.168.137.50:8000 (default: config rover.url)")
    ap.add_argument("--mode", choices=["manual", "guard", "patrol"], help="rover start mode (default: config)")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--save", help="write the annotated video to this .mp4 path")
    ap.add_argument("--record", help="write the RAW camera video (no drawings) to this .mp4 path, at a steady 10 fps - for building training data")
    ap.add_argument("--headless", action="store_true", help="no window (for testing / servers)")
    ap.add_argument("--max-frames", type=int, default=0, help="stop after N frames (0 = no limit)")
    ap.add_argument("--debug", action="store_true", help="show per-person motion values (toggle with V)")
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


class FrameSource:
    """Webcam, video file, or network stream (rover). read() -> (frame or None, ended)."""

    def __init__(self, source, cam_cfg):
        source = str(source)
        self.network = source.startswith(("http://", "https://", "rtsp://"))
        self.webcam = source.isdigit()
        self.live = self.network or self.webcam
        self.mirror = self.webcam and cam_cfg["mirror"]
        self.fps = 25.0
        if self.network:
            from rover_ai.services import LatestFrameReader
            self.reader = LatestFrameReader(source)
            self.seq = 0
            return
        if self.webcam:
            backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
            self.cap = cv2.VideoCapture(int(source), backend)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam_cfg["width"])
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam_cfg["height"])
        else:
            self.cap = cv2.VideoCapture(source)
            self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        if not self.cap.isOpened():
            msg = f"Could not open source: {source}"
            if self.webcam and sys.platform == "darwin":
                msg += ("\nOn macOS, allow camera access for your terminal app in "
                        "System Settings > Privacy & Security > Camera, then restart the terminal.")
            sys.exit(msg)

    def read(self):
        if self.network:
            frame, self.seq = self.reader.read(self.seq, timeout=1.0)
            return frame, False
        ok, frame = self.cap.read()
        if not ok:
            return None, True
        return (cv2.flip(frame, 1) if self.mirror else frame), False

    def release(self):
        if self.network:
            self.reader.alive = False
        else:
            self.cap.release()


def waiting_frame(text):
    img = np.zeros((480, 640, 3), np.uint8)
    cv2.putText(img, text, (20, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def rover_info(controller):
    st = controller.status or {}
    dist = st.get("distance_cm")
    gps = f"{st['lat']:.5f},{st['lon']:.5f}" if st.get("gps_fix") else "no fix"
    flag = "  OBSTACLE" if st.get("obstacle") else ""
    return (f"ROVER {controller.mode.upper()} | {controller.state} | "
            f"sonar {dist if dist else '--'} cm{flag} | GPS {gps}")


def make_fight_detector(fcfg):
    """Trained GRU if available (fight.method: model), otherwise the hand-tuned rules."""
    if fcfg.get("method", "rules") == "model":
        path = ROOT / fcfg.get("model", "models/fight_gru.pt")
        if path.exists():
            from rover_ai.learned import LearnedFightDetector
            det = LearnedFightDetector(fcfg, ROOT)
            print(f"Fight detection: trained model {path.name} (threshold {det.thr:.2f})")
            return det
        print(f"Fight detection: {path.name} not found, using hand-tuned rules (train it: see docs/TRAINING.md)")
    return FightDetector(fcfg)


def run_stream(source, cfg, args, controller=None):
    src = FrameSource(source, cfg["camera"])
    anchor = cfg["zones"]["anchor"]

    detector = Detector(cfg, ROOT)
    zones = Zones(ROOT / cfg["zones"]["file"])
    editor = ZoneEditor(zones)
    alerts = AlertManager(cfg["alerts"], ROOT)
    people = PeopleState()
    loiter = LoiterRule(cfg["loitering"], anchor) if cfg["loitering"]["enabled"] else None
    access = AccessRule(anchor) if cfg["access"]["enabled"] else None
    fight = make_fight_detector(cfg["fight"]) if cfg["fight"]["enabled"] else None
    theft = TheftDetector(cfg["theft"]) if cfg["theft"]["enabled"] else None

    backend = live_view = None
    if cfg["backend"]["url"]:
        from rover_ai.services import BackendLink, LiveView
        backend = BackendLink(cfg["backend"]["url"], cfg["backend"]["device_token"])
        alerts.listeners.append(backend.post_alert)
        live_view = LiveView(cfg["backend"]["live_view_port"])
        print(f"Live view on port {cfg['backend']['live_view_port']}, sending alerts to {cfg['backend']['url']}")

    if not args.headless:
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW, editor.on_mouse)

    writer = None
    recorder, rec_t0, rec_n = None, None, 0
    debug, paused = args.debug, False
    frame_idx, fps, last_tick = 0, None, time.time()
    frame = view = None
    n_people = 0
    while True:
        if not paused:
            frame, ended = src.read()
            if ended:
                break
        if frame is None:
            view = waiting_frame("Waiting for rover video... check the Pi agent and Wi-Fi")
        elif not paused:
            # video files use their own clock so timings are right even if we process slower than real time
            t = time.time() if src.live else frame_idx / src.fps
            editor.shape = frame.shape
            alerts.push_frame(frame, t)

            persons, items, fresh = detector(frame)
            people.update(persons, t)
            tracks = people.visible(t)
            n_people = len(tracks)
            # zones are drawn on the picture, so they only mean something while the camera is parked
            zones_active = controller is None or controller.mode == "guard"
            if controller:
                controller.observe(n_people)
                if theft and controller.camera_moving():
                    theft.reset()      # everything shifts in the picture when the rover moves
            flags = defaultdict(set)
            events, energy = [], {}
            if loiter:
                events += loiter.update(tracks, zones, t, frame.shape, flags)
            if access and zones_active:
                events += access.update(tracks, zones, t, frame.shape, flags)
            if fight:
                ev, energy = fight.update(tracks, t, flags)
                events += ev
            if theft:
                events += theft.update(items, tracks, t, fresh, flags)

            view = frame.copy()
            if zones_active:
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
                if alerts.handle(e, view, t) and controller:
                    controller.on_alert()

            now = time.time()
            inst = 1.0 / max(now - last_tick, 1e-6)
            fps = inst if fps is None else 0.9 * fps + 0.1 * inst
            last_tick = now
            frame_idx += 1
        elif view is None or paused:
            view = frame.copy()
            draw_zones(view, zones, editor)

        shown = view.copy()
        info = rover_info(controller) if controller else None
        draw_hud(shown, alerts.banner(), fps, editor, paused, info=info, rover=controller is not None)
        if live_view:
            live_view.publish(shown)
        if backend:
            backend.status = {"fps": round(fps or 0, 1), "people": n_people,
                              "live_view_port": cfg["backend"]["live_view_port"],
                              "rover": None if controller is None else {
                                  "mode": controller.mode, "state": controller.state,
                                  "online": controller.client.online, **(controller.status or {})}}
            while not backend.commands.empty():
                cmd = backend.commands.get()
                if controller and cmd.get("mode"):
                    controller.set_mode(cmd["mode"])
                if controller and cmd.get("drive"):
                    controller.manual_drive(cmd["drive"])
        if args.record and frame is not None and not paused:
            # constant 10 fps in real time (frames repeated/dropped as needed), so motion speed stays true
            if recorder is None:
                h, w = frame.shape[:2]
                recorder = cv2.VideoWriter(args.record, cv2.VideoWriter_fourcc(*"mp4v"), 10, (w, h))
                rec_t0 = time.time()
            while rec_n < int((time.time() - rec_t0) * 10) + 1:
                recorder.write(frame)
                rec_n += 1
        if args.save:
            if writer is None:
                h, w = shown.shape[:2]
                writer = cv2.VideoWriter(args.save, cv2.VideoWriter_fourcc(*"mp4v"), src.fps if not src.live else 10, (w, h))
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
        elif controller and key in MODE_KEYS:
            controller.set_mode(MODE_KEYS[key])
        elif controller and key in DRIVE_KEYS:
            controller.manual_drive(DRIVE_KEYS[key])
        elif key == ord("z"):
            editor.start("restricted")
        elif key == ord("l"):
            editor.start("loiter")
        elif key == ord("c"):
            zones.clear()
        elif key == ord("v"):
            debug = not debug
        elif key == ord("p"):
            paused = not paused

    if controller:
        controller.client.stop()
    src.release()
    if writer:
        writer.release()
    if recorder:
        recorder.release()
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

    controller = None
    rover_url = args.rover or cfg["rover"]["url"]
    if rover_url:
        from rover_ai.patrol import PatrolController
        from rover_ai.rover_client import RoverClient
        if args.mode:
            cfg["rover"]["start_mode"] = args.mode
        client = RoverClient(rover_url, cfg["rover"]["token"])
        controller = PatrolController(client, cfg["rover"])
        print(f"Rover at {rover_url}, mode: {controller.mode}")

    if args.source is not None:
        source = args.source
    elif controller:
        source = controller.client.stream_url
    else:
        source = str(cfg["camera"]["source"])

    p = Path(source)
    if controller is None and p.is_dir():
        images = sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXT)
        run_images(images, cfg, args)
    elif controller is None and p.suffix.lower() in IMAGE_EXT:
        run_images([p], cfg, args)
    else:
        run_stream(source, cfg, args, controller)


if __name__ == "__main__":
    main()
