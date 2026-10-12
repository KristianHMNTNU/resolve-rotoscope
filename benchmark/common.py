"""Shared helpers for the phase 0 benchmark: frame reading, contrast, overlays."""

import json
import os
import time
from fractions import Fraction

import av
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.expanduser("~/Library/Application Support/Rotoscope")
MODELS_DIR = os.path.join(APP_DIR, "models")
RESULTS_DIR = os.path.join(HERE, "results")
CLIPS_DIR = os.path.dirname(HERE)

# Working resolution handed to every model. The models resize internally
# (to 1024 or less), so 1080p input loses nothing and keeps RAM sane.
WORK_W, WORK_H = 1920, 1080


def load_clips():
    with open(os.path.join(HERE, "clips.json")) as f:
        return json.load(f)


def read_frames(path, start, count, size=(WORK_W, WORK_H)):
    """Decode frames [start, start+count) from the original file as RGB24 arrays.

    Frame numbers are counted from the first frame of the stream. Seeking goes to
    the keyframe before `start` and decodes forward, so the result is exact.
    """
    w, h = size
    frames = []
    with av.open(path) as c:
        v = c.streams.video[0]
        v.thread_type = "AUTO"
        rate = v.average_rate
        tb = v.time_base
        first_pts = v.start_time or 0
        if start > 0:
            c.seek(first_pts + int(start / rate / tb), stream=v, backward=True)
        for fr in c.decode(v):
            idx = int(round((fr.pts - first_pts) * tb * rate))
            if idx < start:
                continue
            frames.append(fr.reformat(width=w, height=h, format="rgb24",
                                      interpolation="AREA").to_ndarray())
            if len(frames) == count:
                break
    return frames


# ── Contrast stretch for the model input ───────────────────────────────────

def contrast_lut(frames, lo_pct=0.5, hi_pct=99.5, max_gain=3.0):
    """Per-clip linear stretch based on luma percentiles, as a 256-entry LUT.

    The same mapping is used for R, G and B so hues are preserved. A clip that
    already spans the full range gives (almost) the identity mapping.
    """
    sample = frames[:: max(1, len(frames) // 20)]
    luma = np.concatenate([
        cv2.cvtColor(cv2.resize(f, (480, 270), interpolation=cv2.INTER_AREA),
                     cv2.COLOR_RGB2GRAY).ravel() for f in sample])
    lo, hi = np.percentile(luma, [lo_pct, hi_pct])
    span = max(hi - lo, 255.0 / max_gain)
    x = np.arange(256, dtype=np.float32)
    lut = np.clip((x - lo) * 255.0 / span, 0, 255).astype(np.uint8)
    return lut, float(lo), float(hi)


def apply_lut(frames, lut):
    return [cv2.LUT(f, lut) for f in frames]


# ── Mask storage ───────────────────────────────────────────────────────────

def save_masks(path, masks):
    """Store a list of HxW uint8 masks (0/255) as bit-packed, compressed npz."""
    m = np.stack([(x > 127) for x in masks])
    np.savez_compressed(path, bits=np.packbits(m, axis=-1), shape=np.array(m.shape))


def load_masks(path):
    d = np.load(path)
    n, h, w = d["shape"]
    return np.unpackbits(d["bits"], axis=-1)[:, :, :w].astype(bool)


# ── Overlay video for visual review ────────────────────────────────────────

def _checker(h, w, sq=24):
    yy, xx = np.mgrid[0:h, 0:w]
    c = (((yy // sq) + (xx // sq)) % 2).astype(np.uint8)
    return np.where(c[..., None] == 1, 200, 120).astype(np.uint8).repeat(3, axis=2)


def write_overlay(path, frames, masks, fps, label="", times_ms=None, half=(1280, 720)):
    """Side-by-side review video: red mask + outline on the original | cut-out on checkerboard."""
    hw, hh = half
    checker = _checker(hh, hw)
    with av.open(path, "w") as out:
        s = out.add_stream("libx264", rate=Fraction(fps).limit_denominator(1001))
        s.width, s.height = hw * 2, hh
        s.pix_fmt = "yuv420p"
        s.options = {"crf": "20", "preset": "veryfast"}
        for i, (f, m) in enumerate(zip(frames, masks)):
            img = cv2.resize(f, (hw, hh), interpolation=cv2.INTER_AREA)
            mk = cv2.resize(m.astype(np.uint8) * 255, (hw, hh), interpolation=cv2.INTER_LINEAR) > 127
            left = img.copy()
            left[mk] = (0.55 * left[mk] + 0.45 * np.array([255, 40, 40])).astype(np.uint8)
            cnts, _ = cv2.findContours(mk.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(left, cnts, -1, (255, 255, 0), 1)
            right = np.where(mk[..., None], img, checker)
            txt = f"{label}  f{i}"
            if times_ms is not None:
                txt += f"  {times_ms[i]:.0f} ms"
            cv2.putText(left, txt, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
            cv2.putText(left, txt, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            frame = av.VideoFrame.from_ndarray(np.hstack([left, right]), format="rgb24")
            for p in s.encode(frame):
                out.mux(p)
        for p in s.encode():
            out.mux(p)


def append_result(row):
    """Append one result row (dict) to results/results.jsonl."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    row = dict(row, timestamp=time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(os.path.join(RESULTS_DIR, "results.jsonl"), "a") as f:
        f.write(json.dumps(row) + "\n")
