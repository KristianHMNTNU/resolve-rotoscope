"""Summarise results/results.jsonl into tables (speed + simple quality indicators).

Quality indicators (no ground truth exists, so these only point at problems; the
overlay videos are what counts):
  stab   mean IoU between consecutive masks (low = flicker / jumps)
  jumps  frames where the mask area changes by more than 25 % from the previous frame
  agree  mean IoU against a reference run on the same clip (--ref)
"""

import argparse
import json
import os
from collections import defaultdict

import numpy as np

from common import RESULTS_DIR, load_masks


def iou(a, b):
    u = np.logical_or(a, b).sum()
    return 1.0 if u == 0 else np.logical_and(a, b).sum() / u


def quality(masks, ref=None):
    small = masks[:, ::4, ::4]
    area = small.reshape(len(small), -1).sum(1).astype(float)
    stab = [iou(small[i - 1], small[i]) for i in range(1, len(small))]
    jumps = int(np.sum(np.abs(np.diff(area)) > 0.25 * np.maximum(area[:-1], 1)))
    out = dict(stab=float(np.mean(stab)) if stab else 1.0, stab_min=float(np.min(stab)) if stab else 1.0,
               jumps=jumps)
    if ref is not None:
        n = min(len(ref), len(small))
        out["agree"] = float(np.mean([iou(small[i], ref[i, ::4, ::4]) for i in range(n)]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filter", default="", help="substring that run names must contain")
    ap.add_argument("--quality", action="store_true", help="compute mask-based indicators")
    ap.add_argument("--ref", default="", help="run name prefix used as reference for 'agree'")
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(os.path.join(RESULTS_DIR, "results.jsonl"))]
    latest = {}
    for r in rows:
        if "error" in r or a.filter not in r.get("name", ""):
            continue
        latest[(r["clip"], r["name"])] = r  # keep the newest run per name

    refs = {}
    print(f"{'clip':5} {'run':44} {'s/frame':>8} {'total':>7} {'RSS MB':>7} {'empty':>5}"
          + (f" {'stab':>5} {'min':>5} {'jumps':>5} {'agree':>5}" if a.quality else ""))
    by_clip = defaultdict(list)
    for (clip, name), r in latest.items():
        by_clip[clip].append(r)
    for clip in sorted(by_clip):
        for r in sorted(by_clip[clip], key=lambda r: r["name"]):
            line = (f"{clip:5} {r['name'][:44]:44} {r['track_ms_mean']/1000:8.2f} {r['total_s']:6.0f}s "
                    f"{r['peak_rss_mb']:7.0f} {r['empty_frames']:5d}")
            if a.quality:
                path = os.path.join(RESULTS_DIR, clip, r["name"] + ".npz")
                if os.path.exists(path):
                    if a.ref and clip not in refs:
                        cand = [x for x in by_clip[clip] if x["name"].startswith(a.ref)]
                        refs[clip] = load_masks(os.path.join(RESULTS_DIR, clip, cand[0]["name"] + ".npz")) if cand else None
                    q = quality(load_masks(path), refs.get(clip))
                    line += f" {q['stab']:5.3f} {q['stab_min']:5.2f} {q['jumps']:5d}"
                    line += f" {q['agree']:5.3f}" if "agree" in q else ""
            print(line)


if __name__ == "__main__":
    main()
