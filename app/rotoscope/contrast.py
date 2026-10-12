"""Gentle per-clip contrast stretch for the model input (helps on flat D-Log footage).

The models always see a stretched copy; the matte is applied to the original
pictures, and the stretched copy is never stored. A clip that already uses the
full range gets an (almost) identity mapping, so no "is this D-Log" switch is needed.
"""

import cv2
import numpy as np


def make_lut(frames, lo_pct=0.5, hi_pct=99.5, max_gain=3.0):
    sample = frames[:: max(1, len(frames) // 20)]
    luma = np.concatenate([
        cv2.cvtColor(cv2.resize(f, (480, 270), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY).ravel()
        for f in sample])
    lo, hi = np.percentile(luma, [lo_pct, hi_pct])
    span = max(hi - lo, 255.0 / max_gain)
    x = np.arange(256, dtype=np.float32)
    return np.clip((x - lo) * 255.0 / span, 0, 255).astype(np.uint8)


def apply(frame, lut):
    return cv2.LUT(frame, lut)
