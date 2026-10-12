"""Edge control: temporal smoothing, shrink/grow and feather.

All sizes are given in pixels of the *source* resolution, so the preview (at
work resolution) and the exported matte (at full resolution) look the same.
"""

import cv2
import numpy as np


def smooth_in_time(probs, i, strength):
    """Weighted average of the soft masks around frame i. strength 0..1."""
    p = probs[i]
    if p is None or strength <= 0:
        return p
    a = 0.5 * strength  # weight of each neighbour; 1.0 -> (0.25, 0.5, 0.25)
    nb = [probs[j] for j in (i - 1, i + 1) if 0 <= j < len(probs) and probs[j] is not None]
    if not nb:
        return p
    acc = p.astype(np.float32) * (1 - a * len(nb))
    for q in nb:
        acc += a * q.astype(np.float32)
    return np.clip(acc, 0, 255).astype(np.uint8)


def finish(prob, out_size, scale, grow_px=0.0, feather_px=0.0):
    """Soft mask (uint8) -> final matte (uint8) at out_size (w, h).

    scale: out pixels per source pixel (1.0 at full res, 0.5 for a half-size preview).
    """
    w, h = out_size
    if prob.shape[1] != w or prob.shape[0] != h:
        prob = cv2.resize(prob, (w, h), interpolation=cv2.INTER_LINEAR)
    m = (prob >= 128).astype(np.uint8) * 255
    r = int(round(abs(grow_px) * scale))
    if r > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        m = cv2.dilate(m, k) if grow_px > 0 else cv2.erode(m, k)
    s = feather_px * scale
    if s > 0.3:
        m = cv2.GaussianBlur(m, (0, 0), s)
    return m
