"""Small helpers for working with COCO-17 keypoints."""
import cv2
import numpy as np

# COCO keypoint indices
NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_ANKLE, R_ANKLE = 15, 16

TORSO = [L_SHOULDER, R_SHOULDER, L_HIP, R_HIP]
WRISTS = [L_WRIST, R_WRIST]
# Legs are left out on purpose: normal walking swings the ankles fast relative to the torso.
ARM_JOINTS = [L_ELBOW, R_ELBOW, L_WRIST, R_WRIST]
ARMS = [(L_SHOULDER, L_ELBOW, L_WRIST), (R_SHOULDER, R_ELBOW, R_WRIST)]

SKELETON = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
            (11, 13), (13, 15), (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4)]

KP_CONF = 0.3


def visible(kpts, idx):
    return kpts[idx, 2] > KP_CONF


def box_center(box):
    return np.array([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2])


def body_center(kpts, box):
    """Mean of visible shoulders/hips; falls back to the box centre."""
    vis = [i for i in TORSO if visible(kpts, i)]
    if vis:
        return kpts[vis, :2].mean(axis=0)
    return box_center(box)


def body_scale(kpts, box):
    """Approximate body width in pixels, used to normalise distances and speeds."""
    cands = []
    if visible(kpts, L_SHOULDER) and visible(kpts, R_SHOULDER):
        cands.append(np.linalg.norm(kpts[L_SHOULDER, :2] - kpts[R_SHOULDER, :2]))
    sh = [i for i in (L_SHOULDER, R_SHOULDER) if visible(kpts, i)]
    hp = [i for i in (L_HIP, R_HIP) if visible(kpts, i)]
    if sh and hp:
        cands.append(0.75 * np.linalg.norm(kpts[sh, :2].mean(0) - kpts[hp, :2].mean(0)))
    if cands:
        return max(max(cands), 1.0)
    return max((box[3] - box[1]) / 4, 1.0)


def feet_point(kpts, box):
    vis = [i for i in (L_ANKLE, R_ANKLE) if visible(kpts, i)]
    if vis:
        return kpts[vis, :2].mean(axis=0)
    return np.array([(box[0] + box[2]) / 2, box[3]])


def anchor_point(kpts, box, mode):
    return feet_point(kpts, box) if mode == "feet" else body_center(kpts, box)


def expand_box(box, frac):
    w, h = box[2] - box[0], box[3] - box[1]
    return np.array([box[0] - w * frac, box[1] - h * frac, box[2] + w * frac, box[3] + h * frac])


def point_in_box(pt, box):
    return box[0] <= pt[0] <= box[2] and box[1] <= pt[1] <= box[3]


def box_iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def joint_angle(a, b, c):
    """Angle at b (degrees) formed by points a-b-c."""
    v1, v2 = a - b, c - b
    n = np.linalg.norm(v1) * np.linalg.norm(v2)
    if n == 0:
        return 0.0
    return float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / n, -1, 1))))


def point_in_polygon(pt, poly):
    return cv2.pointPolygonTest(poly, (float(pt[0]), float(pt[1])), False) >= 0
