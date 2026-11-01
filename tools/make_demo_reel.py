"""Build the demo reel: one video with every scene the rover should react to, for the video-based demo.

The rover's camera films a screen that plays this reel (docs/HOW_IT_WORKS_AND_DEMO.md), so no real people
have to act. Fight and normal scenes are RWF-2000 *test* clips (never used for training); the loitering
scene is the OpenCV pedestrian sample; an optional stealing clip can be added (record one with your phone:
a bag or phone on a table, a hand takes it away).

    python tools/make_demo_reel.py                          # -> data/demo/demo_reel.mp4 + demo_reel.txt
    python tools/make_demo_reel.py --stealing my_clip.mp4   # add your own stealing scene

Needs data/rwf/RWF-2000.tar.gz (the clips are taken out of it once, into data/demo/clips/) and
tests/videos/walking.avi (python tools/get_test_videos.py).
"""
import argparse
import tarfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
W, H, FPS = 1280, 720, 25
CARD_S = 4.0
GAP_S = 3.0          # blank pause between clips: longer than the 2 s after which a person is forgotten, so
                     # nobody's time in view (loitering) carries over from one clip to the next

# (title, what should happen, [(clip, start s, end s)]); "rwf:<split>/<class>/<name>" = clip from the archive
SCENES = [
    ("Scene 1 - Normal activity", "People close together, no fighting: NO alert",
     [("rwf:val/NonFight/DeCoZoey_0", 0, None), ("rwf:val/NonFight/LKdiIccO_0", 0, None),
      ("rwf:val/NonFight/rtSWit6k_0", 0, None)]),
    ("Scene 2 - Fighting", "FIGHT alert naming the two people; rover stops and holds position",
     [("rwf:val/Fight/83d-16REC40_1", 0, None), ("rwf:val/Fight/tf_X7GtOGyE_0", 0, None),
      ("rwf:val/Fight/v4dhdnsxiX4_1", 0, None), ("rwf:val/Fight/TYRFDVD_1105", 0, None)]),
    ("Scene 3 - Loitering / restricted zone", "LOITERING alert when one person stays in view;\n"
     "in guard mode with a zone drawn on the road: UNAUTHORISED ACCESS alerts",
     [("tests/videos/walking.avi", 8, 48)]),
]


def rwf_clips(archive, names, out_dir):
    """Take the needed RWF-2000 clips out of the archive (once)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    want = {f"RWF-2000/{n}.avi": out_dir / f"{n.replace('/', '_')}.avi" for n in names}
    want = {k: v for k, v in want.items() if not v.exists()}
    if want:
        if not archive.exists():
            raise SystemExit(f"Need {archive} to get {len(want)} clips (see docs/TRAINING.md, step 0)")
        print(f"taking {len(want)} clips out of {archive.name} (reads the archive once, a few minutes) ...")
        with tarfile.open(archive, "r|gz") as tar:
            for m in tar:
                if m.name in want and m.isfile():
                    want.pop(m.name).write_bytes(tar.extractfile(m).read())
                    if not want:
                        break
        if want:
            raise SystemExit(f"not found in the archive: {list(want)}")
    return {n: out_dir / f"{n.replace('/', '_')}.avi" for n in names}


def fit(frame):
    """Letterbox any frame into W x H."""
    h, w = frame.shape[:2]
    s = min(W / w, H / h)
    frame = cv2.resize(frame, (int(w * s), int(h * s)))
    canvas = np.zeros((H, W, 3), np.uint8)
    y, x = (H - frame.shape[0]) // 2, (W - frame.shape[1]) // 2
    canvas[y:y + frame.shape[0], x:x + frame.shape[1]] = frame
    return canvas


def text(img, line, y, scale, color=(255, 255, 255), thick=2):
    (tw, _), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    cv2.putText(img, line, ((W - tw) // 2, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def card(title, expected):
    img = np.full((H, W, 3), 20, np.uint8)
    text(img, title, 300, 1.6, thick=3)
    for i, line in enumerate(expected.split("\n")):
        text(img, ("Expected: " if i == 0 else "") + line, 390 + 50 * i, 0.9, (120, 220, 255))
    return img


def gap(title):
    img = np.full((H, W, 3), 20, np.uint8)
    text(img, "next clip", H // 2, 1.0, (150, 150, 150))
    return label(img, title)


def label(frame, line):
    cv2.rectangle(frame, (0, 0), (W, 34), (0, 0, 0), -1)
    cv2.putText(frame, line, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return frame


def clip_frames(path, start, end):
    """Frames of a clip resampled to FPS (so 10, 25 and 30 fps sources all play at real speed)."""
    cap = cv2.VideoCapture(str(path))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or FPS
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    dur = len(frames) / src_fps
    end = dur if end is None else min(end, dur)
    for i in range(int((end - start) * FPS)):
        yield frames[min(int((start + i / FPS) * src_fps), len(frames) - 1)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", default="data/rwf/RWF-2000.tar.gz")
    ap.add_argument("--out", default="data/demo/demo_reel.mp4")
    ap.add_argument("--stealing", help="your own stealing clip (a hand takes a bag / phone / laptop away)")
    args = ap.parse_args()

    scenes = list(SCENES)
    if args.stealing:
        scenes.append(("Scene 4 - Stealing", "STEALING alert naming the person who took the object",
                       [(args.stealing, 0, None)]))
    names = [c[4:] for _, _, clips in scenes for c, _, _ in clips if c.startswith("rwf:")]
    local = rwf_clips(ROOT / args.archive, names, ROOT / "data/demo/clips")

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"avc1"), FPS, (W, H))
    if not writer.isOpened():
        writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    n = 0
    lines = ["Demo reel - scene list (times in the reel)", ""]

    def put(frame, count=1):
        nonlocal n
        for _ in range(count):
            writer.write(frame)
            n += 1

    put(card("AI Surveillance Rover - demo footage",
             "fight / normal scenes = RWF-2000 TEST clips,\nnever used to train the model"), int(CARD_S * FPS))
    for title, expected, clips in scenes:
        t0 = n / FPS
        put(card(title, expected), int(CARD_S * FPS))
        start = n / FPS
        for i, (src, a, b) in enumerate(clips):
            if i:
                put(gap(title), int(GAP_S * FPS))
            path = local[src[4:]] if src.startswith("rwf:") else ROOT / src
            if not Path(path).exists():
                raise SystemExit(f"missing clip {path}")
            for f in clip_frames(path, a, b):
                put(label(fit(f), title))
        lines.append(f"{t0:6.1f}s  card   {title}")
        lines.append(f"{start:6.1f}s - {n / FPS:6.1f}s  {expected.replace(chr(10), ' ')}")
    put(card("End of demo footage", "check the alerts on the phone"), int(CARD_S * FPS))
    writer.release()
    out.with_suffix(".txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"wrote {out} ({n / FPS:.0f} s)")


if __name__ == "__main__":
    main()
