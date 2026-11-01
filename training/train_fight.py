"""Stage 2 of the training pipeline: tracked skeletons -> trained fight model + evaluation.

    python training/train_fight.py            # settings come from params.yaml (section "fight_model")

Data   : data/rwf/pose/train  (RWF-2000 train, 1600 clips) -> 5-fold cross-validation chooses the alert rule
                                                              (threshold + frames in a row), then the final model
                                                              is fitted on 90% with 10% dev for early stopping
         data/rwf/pose/val    (RWF-2000 val, 400 clips)    -> final test, used once, never for training or tuning
Output : models/fight_gru.pt + models/fight_gru.json        (weights + settings/threshold/metrics)
         reports/fight_metrics.json, reports/*.png         (results, curves, confusion matrices)
"""
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
import yaml
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rover_ai.geometry import body_center, body_scale, box_iou  # noqa: E402
from rover_ai.learned import (FPS, MIN_COVER, N_FEAT, PAIR_GAP, FightGRU, flip_boxes, flip_kpts,  # noqa: E402
                               window_features)

P = yaml.safe_load((ROOT / "params.yaml").read_text())["fight_model"]
T, STRIDE, PROX = P["window"], P["stride"], P["proximity"]
NEED_CHOICES = P["consecutive_choices"]        # candidate "judgements in a row" for the alert rule


# ----------------------------------------------------------------------------- loading
def load_clip(path):
    """npz -> {track_id: (kpts (F,17,3), boxes (F,4), present (F,))}, width"""
    d = np.load(path)
    F = int(d["n_frames"])
    tracks = {}
    for tid in np.unique(d["track"]):
        sel = d["track"] == tid
        fr = d["frame"][sel].astype(int)
        if len(fr) < 3:
            continue
        k = np.zeros((F, 17, 3), np.float32)
        b = np.zeros((F, 4), np.float32)
        present = np.zeros(F, bool)
        k[fr], b[fr], present[fr] = d["kpts"][sel], d["box"][sel], True
        idx = np.flatnonzero(present)
        for i0, i1 in zip(idx[:-1], idx[1:]):           # fill gaps by interpolation, like live
            if i1 - i0 > 1:
                w = (np.arange(i0 + 1, i1) - i0) / (i1 - i0)
                k[i0 + 1:i1] = k[i0] * (1 - w)[:, None, None] + k[i1] * w[:, None, None]
                b[i0 + 1:i1] = b[i0] * (1 - w)[:, None] + b[i1] * w[:, None]
        first = idx[0]
        k[:first], b[:first] = k[first], b[first]       # before the track started: repeat its first pose, like live
        alive = np.zeros(F, bool)
        alive[first:idx[-1] + 1] = True                 # tracked from first to last sighting
        tracks[int(tid)] = (k, b, alive, present)
    return tracks, F, float(d["width"])


def close_pairs(tracks, s, e):
    """Pairs of people seen in the window's last frame, tracked for at least MIN_COVER of the window,
    and close to each other (judged at the last frame, like live), closest first."""
    ids = [i for i, (_, _, alive, pr) in tracks.items()
           if pr[e - 1] and alive[s:e].mean() >= MIN_COVER and alive[e - 1]]
    out = []
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            ka, ba = tracks[ids[x]][:2]
            kb, bb = tracks[ids[y]][:2]
            m = e - 1                                     # judge closeness at the window's last frame, like live
            sc = (body_scale(ka[m], ba[m]) + body_scale(kb[m], bb[m])) / 2
            dist = np.linalg.norm(body_center(ka[m], ba[m]) - body_center(kb[m], bb[m])) / sc
            if dist < PROX or box_iou(ba[m], bb[m]) > 0.05:
                out.append((dist, ids[x], ids[y]))
    return [(a, b) for _, a, b in sorted(out)]


def variants(ka, ba, kb, bb, width):
    """Feature windows for the 4 augmentations: as is, mirrored, persons swapped, both."""
    fa, fba, fb, fbb = flip_kpts(ka, width), flip_boxes(ba, width), flip_kpts(kb, width), flip_boxes(bb, width)
    return np.stack([window_features(ka, ba, kb, bb), window_features(fa, fba, fb, fbb),
                     window_features(kb, bb, ka, ba), window_features(fb, fbb, fa, fba)])


def clip_train_windows(path, label):
    """Training windows of one clip: (n, 4 augmentations, T, N_FEAT)."""
    tracks, F, width = load_clip(path)
    X = []
    for s in range(0, F - T + 1, STRIDE):
        pairs = close_pairs(tracks, s, s + T)
        # fight clip: the closest pair is (almost always) the fighters; normal clip: every close pair is a negative
        for a, b in (pairs[:1] if label else pairs):
            ka, ba = tracks[a][:2]
            kb, bb = tracks[b][:2]
            X.append(variants(ka[s:s + T], ba[s:s + T], kb[s:s + T], bb[s:s + T], width))
    return np.array(X, np.float32).reshape(-1, 4, T, N_FEAT)


def stack(per_clip, labels, idx):
    X = np.concatenate([per_clip[i] for i in idx])
    y = np.concatenate([np.full(len(per_clip[i]), labels[i], np.float32) for i in idx])
    return X, y


def clip_window_inputs(path):
    """All windows (stride 1, both person orders) of every close pair, for clip-level scoring like live."""
    tracks, F, _ = load_clip(path)
    per_pair = defaultdict(list)
    for s in range(0, F - T + 1):
        for a, b in close_pairs(tracks, s, s + T):
            ka, ba = tracks[a][:2]
            kb, bb = tracks[b][:2]
            per_pair[(a, b)].append((s, window_features(ka[s:s + T], ba[s:s + T], kb[s:s + T], bb[s:s + T]),
                                     window_features(kb[s:s + T], bb[s:s + T], ka[s:s + T], ba[s:s + T])))
    return per_pair


def pair_probs(model, per_pair):
    """Model probability for every judged window of every pair: {pair: (start frames, probabilities)}."""
    out = {}
    for key, wins in per_pair.items():
        x = torch.from_numpy(np.stack([w[1] for w in wins] + [w[2] for w in wins]))
        with torch.no_grad():
            p = torch.sigmoid(model(x)).numpy()
        out[key] = (np.array([w[0] for w in wins]), (p[:len(wins)] + p[len(wins):]) / 2)
    return out


def clip_score(probs, need):
    """Live rule (rover_ai/learned.py): a pair alerts when its last `need` judgements are all above the threshold;
    its history survives detection gaps of up to PAIR_GAP seconds.
    So the clip score = max over pairs and runs of (min probability over `need` judgements in a row)."""
    best = 0.0
    for starts, p in probs.values():
        run = []
        for s, v in zip(starts, p):
            if run and s - run[-1][0] > PAIR_GAP * FPS:
                run = []
            run.append((s, v))
            if len(run) >= need:
                best = max(best, float(min(r[1] for r in run[-need:])))
    return best


# ----------------------------------------------------------------------------- metrics
def metrics(y, pred, score=None):
    y, pred = np.asarray(y).astype(bool), np.asarray(pred).astype(bool)
    tp, tn = int((y & pred).sum()), int((~y & ~pred).sum())
    fp, fn = int((~y & pred).sum()), int((y & ~pred).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    m = dict(accuracy=(tp + tn) / len(y), precision=prec, recall=rec,
             f1=2 * prec * rec / (prec + rec) if prec + rec else 0.0, tp=tp, tn=tn, fp=fp, fn=fn)
    if score is not None:
        m["roc_auc"] = roc_auc(y, score)
    return {k: round(v, 4) if isinstance(v, float) else v for k, v in m.items()}


def roc_auc(y, s):
    y, s = np.asarray(y, bool), np.asarray(s, float)
    pos, neg = s[y], s[~y]
    if not len(pos) or not len(neg):
        return float("nan")
    return float(((pos[:, None] > neg[None]).sum() + 0.5 * (pos[:, None] == neg[None]).sum()) / (len(pos) * len(neg)))


def rules_baseline(clips):
    """Replay the same skeletons through the original hand-tuned rules (rover_ai/fight.py)."""
    from rover_ai.fight import FightDetector
    from rover_ai.tracks import PeopleState
    from rover_ai.vision import Person
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())["fight"]
    preds = []
    for path, _ in clips:
        d = np.load(path)
        people, det, fired = PeopleState(), FightDetector(cfg), False
        for f in range(int(d["n_frames"])):
            sel = d["frame"] == f
            persons = [Person(int(i), b, float(c), k) for i, b, c, k in
                       zip(d["track"][sel], d["box"][sel], d["conf"][sel], d["kpts"][sel])]
            t = f / 10.0
            people.update(persons, t)
            ev, _ = det.update(people.visible(t), t, defaultdict(set))
            fired = fired or bool(ev)
        preds.append(fired)
    return preds


# ----------------------------------------------------------------------------- training
def train_model(Xf, yf, Xd, yd, log=True):
    """Fit the GRU on (Xf, yf); keep the epoch with the best dev window AUC (early stopping)."""
    model = FightGRU(N_FEAT, P["hidden"], P["layers"], P["dropout"])
    opt = torch.optim.AdamW(model.parameters(), lr=P["lr"], weight_decay=P["weight_decay"])
    pos_w = torch.tensor((1 - yf.mean()) / max(yf.mean(), 1e-6))
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_w)
    Xd_t = torch.from_numpy(Xd[:, 0])
    history, best, best_state, bad = [], -1, None, 0
    for epoch in range(1, P["epochs"] + 1):
        model.train()
        order = np.random.permutation(len(yf))
        total = 0.0
        for i in range(0, len(order), P["batch"]):
            idx = order[i:i + P["batch"]]
            v = np.random.randint(0, 4, len(idx))             # random augmentation per sample
            xb = torch.from_numpy(Xf[idx, v])
            xb = xb + P["noise"] * torch.randn_like(xb)
            loss = loss_fn(model(xb), torch.from_numpy(yf[idx]))
            opt.zero_grad(); loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += float(loss) * len(idx)
        model.eval()
        with torch.no_grad():
            pd = torch.sigmoid(model(Xd_t)).numpy()
        auc = roc_auc(yd, pd)
        history.append(dict(epoch=epoch, loss=total / len(yf), dev_auc=auc))
        if log:
            print(f"epoch {epoch:2d}  loss {total / len(yf):.4f}  dev window AUC {auc:.4f}", flush=True)
        if auc > best:
            best, best_state, bad = auc, {k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            bad += 1
            if bad >= P["patience"]:
                break
    model.load_state_dict(best_state)
    model.eval()
    return model, history, best


def choose_rule(probs, y):
    """Pick (judgements in a row, threshold) with the best accuracy on clips where a close pair was judged.
    Ties go to more judgements and a higher threshold (fewer false alarms)."""
    has = np.array([bool(p) for p in probs])
    grid = np.round(np.arange(0.30, 0.96, 0.01), 2)
    best = None
    for need in NEED_CHOICES:
        scores = np.array([clip_score(p, need) for p in probs])
        for th in grid:
            key = (metrics(y[has], scores[has] >= th)["accuracy"], need, th)
            if best is None or key > best[0]:
                best = (key, scores)
    (_, need, th), scores = best
    return int(need), float(th), scores


def main():
    random.seed(P["seed"]); np.random.seed(P["seed"]); torch.manual_seed(P["seed"])
    roots = [ROOT / P["data"]] + [ROOT / x for x in P.get("extra_data") or []]

    def listing(split):
        return sorted((str(p), int(p.parent.name == "Fight")) for r in roots for p in (r / split).glob("*/*.npz"))

    train_all, test = listing("train"), listing("val")
    random.shuffle(train_all)
    labels = np.array([l for _, l in train_all])
    t0 = time.time()
    per_clip = [clip_train_windows(p, l) for p, l in train_all]
    print(f"clips: train {len(train_all)}, test {len(test)} | windows {sum(map(len, per_clip))} | {time.time() - t0:.0f}s")

    # 1. K-fold cross-validation on the train split: every clip is scored by a model that never saw it
    K = P["folds"]
    fold = np.arange(len(train_all)) % K
    oof = [None] * len(train_all)
    for k in range(K):
        rest = np.flatnonzero(fold != k)
        n_in = len(rest) // 10
        model, hist, auc = train_model(*stack(per_clip, labels, rest[n_in:]), *stack(per_clip, labels, rest[:n_in]), log=False)
        for i in np.flatnonzero(fold == k):
            oof[i] = pair_probs(model, clip_window_inputs(train_all[i][0]))
        print(f"fold {k + 1}/{K}: {len(hist)} epochs, dev window AUC {auc:.3f} | {time.time() - t0:.0f}s", flush=True)
    need, thr, cv_scores = choose_rule(oof, labels)
    cv_has = np.array([bool(p) for p in oof])
    cv = metrics(labels[cv_has], cv_scores[cv_has] >= thr, cv_scores[cv_has])
    print(f"alert rule: {need} judgements in a row >= {thr:.2f} | cross-validation (clips with a pair): {cv}")

    # 2. final model: 90% fit, 10% dev for early stopping
    n_dev = max(1, len(train_all) // 10)
    idx = np.arange(len(train_all))
    model, history, best = train_model(*stack(per_clip, labels, idx[n_dev:]), *stack(per_clip, labels, idx[:n_dev]))
    windows = int(sum(len(per_clip[i]) for i in idx[n_dev:]))
    del per_clip

    # 3. final test on RWF-2000 val (never seen during training or rule choice), scored like live
    inputs = [clip_window_inputs(p) for p, _ in test]
    test_scores = np.array([clip_score(pair_probs(model, x), need) for x in inputs])
    has_pair = np.array([len(x) > 0 for x in inputs])      # two people close together were judged at least once
    test_y = np.array([l for _, l in test])
    rule_pred = np.array(rules_baseline(test))
    learned = metrics(test_y, test_scores >= thr, test_scores)
    rules = metrics(test_y, rule_pred)
    hp = has_pair
    learned_pair = metrics(test_y[hp], test_scores[hp] >= thr, test_scores[hp])
    rules_pair = metrics(test_y[hp], rule_pred[hp])
    no_pair = int((test_y.astype(bool) & ~has_pair).sum())
    coverage = dict(test_clips=len(test), with_close_pair=int(hp.sum()),
                    fight_with_pair=int((test_y.astype(bool) & hp).sum()), nonfight_with_pair=int((~test_y.astype(bool) & hp).sum()))
    print("TEST learned:", learned)
    print("TEST rules  :", rules)
    print("coverage:", coverage)
    print("TEST (clips with a close pair) learned:", learned_pair)
    print("TEST (clips with a close pair) rules  :", rules_pair)

    out = ROOT / "models" / "fight_gru.pt"
    torch.save(model.state_dict(), out)
    meta = dict(arch=dict(n_feat=N_FEAT, hidden=P["hidden"], layers=P["layers"], dropout=P["dropout"]),
                window=T, fps=FPS, threshold=thr, consecutive=need, pair_gap=PAIR_GAP, proximity=PROX,
                trained_on="RWF-2000 train split (pose from YOLO11n-pose + ByteTrack)",
                clips=dict(train=len(train_all), fit=len(train_all) - n_dev, dev=n_dev, test=len(test), folds=K),
                windows=windows, cv_with_pair=cv, test_learned=learned, test_rules=rules, coverage=coverage,
                test_learned_with_pair=learned_pair, test_rules_with_pair=rules_pair, created=time.strftime("%Y-%m-%d %H:%M"))
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2))

    rep = ROOT / "reports"
    rep.mkdir(exist_ok=True)
    (rep / "fight_metrics.json").write_text(json.dumps(
        dict(threshold=thr, consecutive=need, cv_with_pair=cv, test_learned=learned, test_rules=rules, coverage=coverage,
             test_learned_with_pair=learned_pair, test_rules_with_pair=rules_pair, dev_window_auc=best,
             test_fight_clips_without_close_pair=no_pair, history=history), indent=2))
    plots(rep, history, test_y[hp], test_scores[hp], thr, learned_pair, rules_pair)
    log_mlflow(meta, history)
    print(f"saved {out} and reports/ in {(time.time() - t0) / 60:.1f} min")


def plot_training(rep, history, best_epoch=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ep = [h["epoch"] for h in history]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7, 2.9))
    a1.plot(ep, [h["loss"] for h in history], color="#2a6fdb", lw=2)
    a1.set_title("Training loss", fontsize=10); a1.set_xlabel("epoch")
    a2.plot(ep, [h["dev_auc"] for h in history], color="#2a6fdb", lw=2)
    best = max(history, key=lambda h: h["dev_auc"])
    a2.scatter([best["epoch"]], [best["dev_auc"]], color="#e07b00", zorder=3, s=40)
    a2.annotate(f"kept: epoch {best['epoch']}", (best["epoch"], best["dev_auc"]), textcoords="offset points",
                xytext=(8, -4), fontsize=9, va="top")
    a2.set_title("Dev window ROC AUC", fontsize=10); a2.set_xlabel("epoch")
    for a in (a1, a2):
        a.grid(alpha=0.25); a.spines[["top", "right"]].set_visible(False)
        a.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    fig.tight_layout(); fig.savefig(rep / "fight_training.png", dpi=200)
    plt.close(fig)


def plots(rep, history, y, scores, thr, learned, rules):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plot_training(rep, history)

    y, s = np.asarray(y, bool), np.asarray(scores)
    ths = np.unique(np.concatenate([[0, 1.01], s]))
    tpr = [((s >= t) & y).sum() / y.sum() for t in ths]
    fpr = [((s >= t) & ~y).sum() / (~y).sum() for t in ths]
    fig, ax = plt.subplots(figsize=(4, 4))
    ax.plot(fpr, tpr, color="#2a6fdb", lw=2, label=f"trained GRU (AUC {learned['roc_auc']:.2f})")
    ax.plot([0, 1], [0, 1], color="#999", lw=1, ls="--")
    ax.scatter([rules["fp"] / (rules["fp"] + rules["tn"])], [rules["recall"]], color="#e07b00", zorder=3, s=40,
               label="hand-tuned rules")
    ax.set_xlabel("false positive rate"); ax.set_ylabel("true positive rate"); ax.legend(loc="lower right", frameon=False)
    ax.set_title(f"Test clips with a close pair (n = {len(y)})", fontsize=10)
    fig.tight_layout(); fig.savefig(rep / "fight_roc.png", dpi=200); plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(7, 3.2))
    for ax, (name, m) in zip(axes, (("Trained GRU", learned), ("Hand-tuned rules", rules))):
        cm = np.array([[m["tn"], m["fp"]], [m["fn"], m["tp"]]])
        ax.imshow(cm, cmap="Blues", vmin=0, vmax=cm.sum() / 2)
        for i in range(2):
            for j in range(2):
                ax.text(j, i, cm[i, j], ha="center", va="center", fontsize=13,
                        color="white" if cm[i, j] > cm.sum() / 4 else "black")
        ax.set_xticks([0, 1], ["normal", "fight"]); ax.set_yticks([0, 1], ["normal", "fight"])
        ax.set_xlabel("predicted"); ax.set_ylabel("actual"); ax.set_title(f"{name}\naccuracy {m['accuracy']:.1%}", fontsize=10)
    fig.suptitle(f"RWF-2000 test clips where two people were close together (n = {len(y)})", fontsize=10)
    fig.tight_layout(); fig.savefig(rep / "fight_confusion.png", dpi=200); plt.close(fig)


def log_mlflow(meta, history):
    """Log the run to MLflow if it is installed (pip install mlflow; view with: mlflow ui)."""
    try:
        import mlflow
    except ImportError:
        return
    mlflow.set_tracking_uri((ROOT / "mlruns").as_uri())
    mlflow.set_experiment("fight-model")
    with mlflow.start_run():
        mlflow.log_params(P)
        for h in history:
            mlflow.log_metrics({"loss": h["loss"], "dev_auc": h["dev_auc"]}, step=h["epoch"])
        mlflow.log_metrics({f"test_{k}": v for k, v in meta["test_learned"].items()})
        mlflow.log_artifact(str(ROOT / "models" / "fight_gru.pt"))
        mlflow.log_artifact(str(ROOT / "models" / "fight_gru.json"))


if __name__ == "__main__":
    main()
