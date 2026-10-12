"""Benchmark SAM 2.1 tiny video tracking via ONNX Runtime.

Uses the square-zero-labs/sam2.1-tiny-video-onnx export (fixed 7-block memory bank,
16 object pointers). The memory bank is assembled here, following transformers'
Sam2VideoModel (_gather_memory_frame_outputs / _get_object_pointers).

Example:
  python bench_onnx.py --clip 0255 --provider cpu coreml --contrast on --frames 30
"""

import argparse
import json
import os
import resource
import time

import cv2
import numpy as np
import onnxruntime as ort

from common import (CLIPS_DIR, MODELS_DIR, RESULTS_DIR, append_result, apply_lut, contrast_lut,
                    load_clips, read_frames, save_masks, write_overlay)

ONNX_DIR = os.path.join(MODELS_DIR, "onnx_sam21_tiny")
NUM_MASKMEM = 7
MAX_PTRS = 16


class Sam2Onnx:
    def __init__(self, provider, threads):
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if provider == "coreml":
            providers = [("CoreMLExecutionProvider", {"MLComputeUnits": "ALL"}), "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]

        def load(name, prov=providers):
            return ort.InferenceSession(os.path.join(ONNX_DIR, name + ".onnx"), so, providers=prov)

        # Only the image encoder is worth trying on CoreML; the rest stays on CPU.
        self.enc = load("vision_encoder")
        self.dec = load("mask_decoder", ["CPUExecutionProvider"])
        self.menc = load("memory_encoder", ["CPUExecutionProvider"])
        self.matt = load("memory_attention", ["CPUExecutionProvider"])
        self.tpos = load("pointer_tpos", ["CPUExecutionProvider"])
        c = json.load(open(os.path.join(ONNX_DIR, "constants.json")))
        self.tpe = np.array(c["memory_temporal_positional_encoding"], np.float32)  # (7, 64)
        self.mean = np.array(c["image_mean"], np.float32) * 255
        self.std = np.array(c["image_std"], np.float32) * 255

    def preprocess(self, rgb):
        x = cv2.resize(rgb, (1024, 1024), interpolation=cv2.INTER_AREA).astype(np.float32)
        x = (x - self.mean) / self.std
        return x.transpose(2, 0, 1)[None]

    def track(self, frames, pos, neg, timings):
        """Yield one low-res (256x256) logit map per frame. Prompt is on frame 0."""
        h, w = frames[0].shape[:2]
        n_total = len(frames)
        max_ptrs = min(n_total, MAX_PTRS)
        cond = None
        noncond = {}
        for t, f in enumerate(frames):
            tm = {}
            t0 = time.perf_counter()
            feats0, feats1, feats2, feats2_no_mem, vpos = self.enc.run(None, {"pixel_values": self.preprocess(f)})
            tm["encoder"] = time.perf_counter() - t0

            t0 = time.perf_counter()
            if t == 0:
                pts = [(x * 1024 / w, y * 1024 / h) for x, y in pos + neg]
                lbl = [1] * len(pos) + [0] * len(neg)
                feed_cond = feats2_no_mem
                points = np.array(pts, np.float32)[None, None]
                labels = np.array(lbl, np.int32)[None, None]
            else:
                feed_cond = self._condition(t, feats2, vpos, cond, noncond, max_ptrs)
                points = np.zeros((1, 1, 1, 2), np.float32)
                labels = -np.ones((1, 1, 1), np.int32)
            tm["mem_attn"] = time.perf_counter() - t0

            t0 = time.perf_counter()
            low, high, iou, osl, ptr = self.dec.run(None, {
                "feats0": feats0, "feats1": feats1, "feats2_cond": feed_cond,
                "input_points": points, "input_labels": labels})
            tm["decoder"] = time.perf_counter() - t0

            t0 = time.perf_counter()
            mtok, mpos = self.menc.run(None, {
                "feats2": feats2, "high_res_mask": high, "object_score_logits": osl.reshape(1, 1),
                "binarize": np.array(1.0 if t == 0 else 0.0, np.float32)})
            tm["mem_enc"] = time.perf_counter() - t0
            entry = dict(tok=mtok, pos=mpos, ptr=ptr.reshape(256))
            if t == 0:
                cond = entry
            else:
                noncond[t] = entry
                noncond.pop(t - MAX_PTRS, None)
            timings.append(tm)
            yield low[0, 0]

    def _condition(self, t, feats2, vpos, cond, noncond, max_ptrs):
        # Spatial memory: conditioning frame (temporal PE row 6), then offsets 6..1 (row offset-1).
        toks, poss = [cond["tok"]], [cond["pos"] + self.tpe[NUM_MASKMEM - 1]]
        for off in range(NUM_MASKMEM - 1, 0, -1):
            e = noncond.get(t - off)
            if e is not None:
                toks.append(e["tok"])
                poss.append(e["pos"] + self.tpe[off - 1])
        while len(toks) < NUM_MASKMEM:  # fixed-size graph: pad with the most recent block
            toks.append(toks[-1])
            poss.append(poss[-1])

        # Object pointers: conditioning frame first, then the most recent tracked frames.
        diffs, ptrs = [t], [cond["ptr"]]
        for d in range(1, max_ptrs):
            e = noncond.get(t - d)
            if t - d < 0:
                break
            if e is not None:
                diffs.append(d)
                ptrs.append(e["ptr"])
        recent = 1 if len(ptrs) > 1 else 0
        while len(ptrs) < MAX_PTRS:
            diffs.append(diffs[recent])
            ptrs.append(ptrs[recent])
        norm = np.array(diffs, np.float32) / max(max_ptrs - 1, 1)
        ppos = self.tpos.run(None, {"normalized_diffs": norm})[0]          # (16, 64)
        ptok = np.stack(ptrs).reshape(MAX_PTRS * 4, 1, 64)                 # each pointer -> 4 tokens
        ppos = np.repeat(ppos, 4, axis=0).reshape(MAX_PTRS * 4, 1, 64)

        memory = np.concatenate(toks + [ptok], axis=0).astype(np.float32)
        memory_pos = np.concatenate(poss + [ppos], axis=0).astype(np.float32)
        cur = feats2.reshape(256, -1).T[:, None, :].copy()
        cur_pos = vpos.reshape(256, -1).T[:, None, :].copy()
        return self.matt.run(None, {"current_vision_features": cur, "current_vision_position_embeddings": cur_pos,
                                    "memory": memory, "memory_pos": memory_pos})[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True)
    ap.add_argument("--provider", nargs="+", default=["cpu"], choices=["cpu", "coreml"])
    ap.add_argument("--contrast", nargs="+", default=["on"], choices=["on", "off"])
    ap.add_argument("--frames", type=int, default=0)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--overlay", action="store_true")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()

    clip = load_clips()[a.clip]
    count = a.frames or clip["count"]
    orig = read_frames(os.path.join(CLIPS_DIR, clip["file"]), clip["start"], count)
    lut, lo, hi = contrast_lut(orig)
    h, w = orig[0].shape[:2]
    print(f"[{a.clip}] {len(orig)} frames; contrast lo={lo:.0f} hi={hi:.0f}")
    out_dir = os.path.join(RESULTS_DIR, a.clip)
    os.makedirs(out_dir, exist_ok=True)

    for provider in a.provider:
        model = Sam2Onnx(provider, a.threads)
        for contrast in a.contrast:
            frames = apply_lut(orig, lut) if contrast == "on" else orig
            name = f"sam2.1-tiny-onnx_{provider}_1024_c{contrast}{a.tag}"
            print(f"  {name} ...", flush=True)
            timings, masks, ms = [], [], []
            t_all = time.perf_counter()
            t0 = time.perf_counter()
            for low in model.track(frames, clip["pos"], clip["neg"], timings):
                masks.append((cv2.resize(low, (w, h), interpolation=cv2.INTER_LINEAR) > 0).astype(np.uint8) * 255)
                ms.append((time.perf_counter() - t0) * 1000)
                t0 = time.perf_counter()
            total = time.perf_counter() - t_all
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            ms = np.array(ms)
            track = ms[1:] if len(ms) > 1 else ms
            parts = {k: float(np.mean([x[k] for x in timings[1:] or timings]) * 1000) for k in timings[0]}
            cover = [float((m > 127).mean()) for m in masks]
            row = dict(clip=a.clip, engine="onnxruntime", model="sam2.1-tiny", backend=provider, img_size=1024,
                       contrast=contrast, threads=a.threads, frames=len(ms), first_ms=float(ms[0]),
                       track_ms_mean=float(track.mean()), track_ms_median=float(np.median(track)),
                       total_s=total, peak_rss_mb=rss / 1e6, parts_ms=parts,
                       empty_frames=int(sum(c < 1e-4 for c in cover)), name=name)
            append_result(row)
            print(f"    track {track.mean():.0f} ms/frame (median {np.median(track):.0f}), total {total:.1f}s, "
                  f"rss {rss/1e6:.0f} MB, empty {row['empty_frames']}  parts "
                  + ", ".join(f"{k} {v:.0f}" for k, v in parts.items()))
            save_masks(os.path.join(out_dir, name + ".npz"), masks)
            if a.overlay:
                write_overlay(os.path.join(out_dir, name + ".mp4"), orig, masks, 25, label=name, times_ms=ms)


if __name__ == "__main__":
    main()
