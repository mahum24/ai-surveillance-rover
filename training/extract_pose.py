"""Stage 1 of the training pipeline: video clips -> tracked skeletons.

Reads RWF-2000 straight out of its .tar.gz (no need to unpack 12 GB), runs the SAME
YOLO11n-pose + ByteTrack used live, at 10 frames per second, and saves one small .npz per clip:

    data/rwf/pose/<split>/<Fight|NonFight>/<clip>.npz
        frame (N,)  track (N,)  box (N,4)  conf (N,)  kpts (N,17,3)  n_frames  width  height

    python training/extract_pose.py --archive data/rwf/RWF-2000.tar.gz --out data/rwf/pose

Your own clips (e.g. rover recordings) laid out the same way, <split>/<Fight|NonFight>/<clip>.mp4:
    python training/extract_pose.py --folder data/own --out data/own/pose
Already-processed clips are skipped, so it can be stopped and restarted.
"""
import argparse
import os
import tarfile
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TARGET_FPS = 10.0
_model = None


def _init(threads):
    global _model
    import torch
    torch.set_num_threads(threads)
    from ultralytics import YOLO
    _model = YOLO(str(ROOT / "models" / "yolo11n-pose.pt"))


def process(video, out, delete=True, imgsz=480, conf=0.4):
    import cv2
    if _model.predictor is not None and getattr(_model.predictor, "trackers", None):
        for tr in _model.predictor.trackers:
            tr.reset()                          # fresh IDs for every clip
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = fps / TARGET_FPS
    rows, i, next_keep, kept, w, h = [], 0, 0.0, 0, 0, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i + 1e-6 >= next_keep:
            next_keep += step
            h, w = frame.shape[:2]
            r = _model.track(frame, persist=True, tracker="bytetrack.yaml", imgsz=imgsz, conf=conf, verbose=False)[0]
            if r.boxes is not None and len(r.boxes) and r.boxes.id is not None:
                for tid, b, c, k in zip(r.boxes.id.int().cpu().numpy(), r.boxes.xyxy.cpu().numpy(),
                                        r.boxes.conf.cpu().numpy(), r.keypoints.data.cpu().numpy()):
                    rows.append((kept, int(tid), b, float(c), k))
            kept += 1
        i += 1
    cap.release()
    if delete:
        os.unlink(video)
    np.savez_compressed(
        out,
        frame=np.array([r[0] for r in rows], np.int16),
        track=np.array([r[1] for r in rows], np.int32),
        box=np.array([r[2] for r in rows], np.float32).reshape(-1, 4),
        conf=np.array([r[3] for r in rows], np.float32),
        kpts=np.array([r[4] for r in rows], np.float32).reshape(-1, 17, 3),
        n_frames=kept, width=w, height=h)
    return out, kept, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", default="data/rwf/RWF-2000.tar.gz")
    ap.add_argument("--folder", help="instead of the archive: a folder of <split>/<Fight|NonFight>/<clip> videos")
    ap.add_argument("--out", default="data/rwf/pose")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--threads", type=int, default=2, help="torch threads per worker")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many clips (0 = all)")
    args = ap.parse_args()

    out_root = Path(args.out)
    if args.folder:
        return extract_folder(Path(args.folder), out_root, args)
    tmp = Path(tempfile.mkdtemp(prefix="rwf_", dir=out_root.parent))
    done = skipped = 0
    t0 = time.time()
    pending = set()
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(args.threads,)) as pool, \
            tarfile.open(args.archive, "r|gz") as tar:
        for m in tar:
            if not (m.isfile() and m.name.lower().endswith(".avi")):
                continue
            parts = Path(m.name).parts           # .../<train|val>/<Fight|NonFight>/<clip>.avi
            split, label, name = parts[-3], parts[-2], Path(parts[-1]).stem
            if split not in ("train", "val") or label not in ("Fight", "NonFight"):
                continue
            dst = out_root / split / label / f"{name}.npz"
            if dst.exists():
                skipped += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            video = tmp / f"{split}_{label}_{name}.avi"     # our own file name, never a path from the archive
            with tar.extractfile(m) as src, open(video, "wb") as f:
                f.write(src.read())
            pending.add(pool.submit(process, str(video), str(dst)))
            while len(pending) >= 2 * args.workers:     # keep only a few clips on disk at a time
                fin, pending = wait(pending, return_when=FIRST_COMPLETED)
                for f in fin:
                    f.result()
                    done += 1
                    if done % 20 == 0:
                        rate = done / (time.time() - t0)
                        print(f"{done} clips ({skipped} skipped), {rate * 60:.0f}/min", flush=True)
            if args.limit and done + len(pending) >= args.limit:
                break
        for f in pending:
            f.result()
            done += 1
    tmp.rmdir()
    print(f"finished: {done} new clips, {skipped} already done, {(time.time() - t0) / 60:.1f} min")


def extract_folder(folder, out_root, args):
    videos = [v for v in sorted(folder.glob("*/*/*")) if v.suffix.lower() in (".mp4", ".avi", ".mov", ".mkv")
              and v.parts[-3] in ("train", "val") and v.parts[-2] in ("Fight", "NonFight")]
    todo = [(v, out_root / v.parts[-3] / v.parts[-2] / f"{v.stem}.npz") for v in videos]
    todo = [(v, d) for v, d in todo if not d.exists()]
    print(f"{len(videos)} videos, {len(todo)} to process")
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(args.threads,)) as pool:
        for v, d in todo:
            d.parent.mkdir(parents=True, exist_ok=True)
        for out, kept, n in pool.map(process, [str(v) for v, _ in todo], [str(d) for _, d in todo], [False] * len(todo)):
            print(f"{Path(out).name}: {kept} frames, {n} detections", flush=True)


if __name__ == "__main__":
    main()
