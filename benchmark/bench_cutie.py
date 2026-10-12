"""Benchmark Cutie (PyTorch 2.2, CPU). The first mask comes from SAM 2.1 small (sam3.cpp).

Example:
  python bench_cutie.py --clip 0255 --model base small --size 512 768 1024 --contrast on off --frames 30
"""

import argparse
import os
import resource
import sys
import time

import numpy as np
import torch

from common import (APP_DIR, CLIPS_DIR, MODELS_DIR, RESULTS_DIR, append_result, apply_lut,
                    contrast_lut, load_clips, load_masks, read_frames, save_masks, write_overlay)

CUTIE_DIR = os.path.join(APP_DIR, "vendor", "Cutie")
sys.path.insert(0, CUTIE_DIR)

from hydra import compose, initialize_config_dir  # noqa: E402
from cutie.inference.inference_core import InferenceCore  # noqa: E402
from cutie.inference.utils.args_utils import get_dataset_cfg  # noqa: E402
from cutie.model.cutie import CUTIE  # noqa: E402

FIRST_MASK_MODEL = "sam2.1_hiera_small_f16"


def first_mask(clip_id, clip, frame0, contrast):
    """Mask on frame 0 from the clicks, via SAM 2.1 small. Cached per clip/contrast."""
    path = os.path.join(RESULTS_DIR, clip_id, f"firstmask_{FIRST_MASK_MODEL}_c{contrast}.npz")
    if os.path.exists(path):
        return load_masks(path)[0], None
    from bench_sam3 import run_one
    r = run_one([frame0], clip, FIRST_MASK_MODEL, "cpu", 0, 4)
    save_masks(path, r["masks"])
    return r["masks"][0] > 127, r["model_ms"][0]


def load_cutie(variant):
    with initialize_config_dir(version_base="1.3.2", config_dir=os.path.join(CUTIE_DIR, "cutie", "config")):
        cfg = compose(config_name="eval_config", overrides=[f"model={variant}"])
    get_dataset_cfg(cfg)
    net = CUTIE(cfg).eval()
    weights = torch.load(os.path.join(MODELS_DIR, "cutie", f"cutie-{variant}-mega.pth"), map_location="cpu")
    net.load_weights(weights)
    return net, cfg


def run_one(net, cfg, frames, mask0, short_side):
    proc = InferenceCore(net, cfg=cfg)
    proc.max_internal_size = short_side
    masks, ms = [], []
    for i, f in enumerate(frames):
        t0 = time.perf_counter()
        img = torch.from_numpy(f).permute(2, 0, 1).float() / 255
        if i == 0:
            prob = proc.step(img, torch.from_numpy(mask0.astype(np.int64)), objects=[1])
        else:
            prob = proc.step(img, end=(i == len(frames) - 1))
        m = proc.output_prob_to_mask(prob).numpy().astype(np.uint8) * 255
        ms.append((time.perf_counter() - t0) * 1000)
        masks.append(m)
    return masks, ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True)
    ap.add_argument("--model", nargs="+", default=["base"], choices=["base", "small"])
    ap.add_argument("--size", nargs="+", type=int, default=[768], help="longest side of the internal image")
    ap.add_argument("--contrast", nargs="+", default=["on"], choices=["on", "off"])
    ap.add_argument("--frames", type=int, default=0)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--overlay", action="store_true")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()

    torch.set_grad_enabled(False)
    torch.set_num_threads(a.threads)
    clip = load_clips()[a.clip]
    count = a.frames or clip["count"]
    orig = read_frames(os.path.join(CLIPS_DIR, clip["file"]), clip["start"], count)
    lut, lo, hi = contrast_lut(orig)
    print(f"[{a.clip}] {len(orig)} frames; contrast lo={lo:.0f} hi={hi:.0f}")
    h, w = orig[0].shape[:2]
    out_dir = os.path.join(RESULTS_DIR, a.clip)
    os.makedirs(out_dir, exist_ok=True)

    nets = {v: load_cutie(v) for v in a.model}
    for contrast in a.contrast:
        frames = apply_lut(orig, lut) if contrast == "on" else orig
        mask0, sam_ms = first_mask(a.clip, clip, frames[0], contrast)
        if sam_ms:
            print(f"  first mask (SAM 2.1 small) {sam_ms:.0f} ms")
        for variant in a.model:
            net, cfg = nets[variant]
            for size in a.size:
                short = round(size * min(h, w) / max(h, w))
                name = f"cutie-{variant}_cpu_{size}_c{contrast}{a.tag}"
                print(f"  {name} (short side {short}) ...", flush=True)
                rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                t0 = time.perf_counter()
                masks, ms = run_one(net, cfg, frames, mask0, short)
                total = time.perf_counter() - t0
                rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                ms = np.array(ms)
                track = ms[1:] if len(ms) > 1 else ms
                cover = [float((m > 127).mean()) for m in masks]
                row = dict(clip=a.clip, engine="cutie", model=f"cutie-{variant}", backend="cpu",
                           img_size=size, contrast=contrast, threads=a.threads, frames=len(ms),
                           first_ms=float(ms[0]), track_ms_mean=float(track.mean()),
                           track_ms_median=float(np.median(track)), total_s=total,
                           peak_rss_mb=rss / 1e6, rss_growth_mb=(rss - rss0) / 1e6,
                           empty_frames=int(sum(c < 1e-4 for c in cover)), name=name)
                append_result(row)
                print(f"    track {track.mean():.0f} ms/frame (median {np.median(track):.0f}), "
                      f"total {total:.1f}s, process peak rss {rss/1e6:.0f} MB, empty {row['empty_frames']}")
                save_masks(os.path.join(out_dir, name + ".npz"), masks)
                if a.overlay:
                    write_overlay(os.path.join(out_dir, name + ".mp4"), orig, masks, 25,
                                  label=name, times_ms=ms)


if __name__ == "__main__":
    main()
