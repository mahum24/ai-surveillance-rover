"""Checks for the learned fight model code (rover_ai/learned.py). No camera or dataset needed.

    python tests/test_learned.py
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rover_ai.learned import FPS, N_FEAT, FightGRU, flip_boxes, flip_kpts, resample_track, window_features  # noqa: E402


def person(x, T=20, wave=0.0):
    """A standing skeleton centred at x (pixels), optionally waving its right wrist."""
    k = np.zeros((T, 17, 3), np.float32)
    base = {5: (-20, -60), 6: (20, -60), 7: (-25, -30), 8: (25, -30), 9: (-25, 0), 10: (25, 0),
            11: (-15, 0), 12: (15, 0), 13: (-15, 50), 14: (15, 50), 15: (-15, 100), 16: (15, 100), 0: (0, -90)}
    for i, (dx, dy) in base.items():
        k[:, i] = (x + dx, 200 + dy, 0.9)
    k[:, 10, 0] += wave * np.sin(np.arange(T))
    b = np.tile(np.array([x - 30, 100, x + 30, 310], np.float32), (T, 1))
    return k, b


def check(name, ok):
    print(("[PASS] " if ok else "[FAIL] ") + name)
    return ok


def main():
    results = []
    ka, ba = person(300)
    kb, bb = person(360, wave=30)
    f = window_features(ka, ba, kb, bb)
    results.append(check(f"feature shape is (20, {N_FEAT})", f.shape == (20, N_FEAT) and np.isfinite(f).all()))

    # moving both people sideways together (camera pan / walking side by side) must not change the features
    f2 = window_features(ka + [50, 0, 0], ba + 50 * np.array([1, 0, 1, 0]), kb + [50, 0, 0], bb + 50 * np.array([1, 0, 1, 0]))
    results.append(check("features ignore where the pair is in the picture", np.allclose(f, f2, atol=1e-4)))

    # the same scene twice as large (people closer to the camera) gives the same features
    f3 = window_features(ka * [2, 2, 1], ba * 2, kb * [2, 2, 1], bb * 2)
    results.append(check("features ignore distance to the camera (scale)", np.allclose(f, f3, atol=1e-4)))

    # mirroring twice gives back the original
    results.append(check("mirroring twice is the identity",
                         np.allclose(flip_kpts(flip_kpts(ka, 640), 640), ka) and np.allclose(flip_boxes(flip_boxes(ba, 640), 640), ba)))

    # a waving arm creates arm velocity features, a still person none
    still = window_features(ka, ba, *person(360))
    results.append(check("arm motion shows up in the velocity features", np.abs(f).sum() > np.abs(still).sum()))

    # the network runs and gives a probability
    import torch
    m = FightGRU().eval()
    with torch.no_grad():
        p = torch.sigmoid(m(torch.from_numpy(f[None]))).item()
    results.append(check("model outputs a probability", 0.0 <= p <= 1.0))

    # live resampling: a 12 fps track is resampled to 20 frames at 10 fps; a track younger than 1 s is refused
    hist = [(i / 12, ka[0], ba[0]) for i in range(30)]
    w = resample_track(hist, hist[-1][0], 20)
    results.append(check("live track resampled to 20 frames at 10 fps", w is not None and w[0].shape == (20, 17, 3)))
    young = [(i / 12, ka[0], ba[0]) for i in range(8)]          # 0.6 s of history
    results.append(check("a track seen for less than 1 s is not judged yet", resample_track(young, young[-1][0], 20) is None))
    results.append(check("model works at 10 fps", FPS == 10.0))

    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
