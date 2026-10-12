"""Benchmark sam3.cpp models (EdgeTAM, SAM 2.1 tiny/small) through rs_track.

Example:
  python bench_sam3.py --clip 0255 --model edgetam_f16 sam2.1_hiera_tiny_f16 \
      --backend cpu metal --img-size 1024 --contrast on off --frames 30
"""

import argparse
import os
import subprocess
import time

import numpy as np

from common import (APP_DIR, CLIPS_DIR, MODELS_DIR, RESULTS_DIR, WORK_H, WORK_W,
                    append_result, apply_lut, contrast_lut, load_clips, read_frames,
                    save_masks, write_overlay)

RS_TRACK = os.path.join(APP_DIR, "bin", "rs_track")


def run_one(frames, clip, model, backend, img_size, threads):
    n = len(frames)
    cmd = [RS_TRACK, "--model", os.path.join(MODELS_DIR, "ggml", model + ".ggml"),
           "--width", str(WORK_W), "--height", str(WORK_H), "--frames", str(n),
           "--threads", str(threads)]
    if backend == "cpu":
        cmd.append("--cpu")
    if img_size:
        cmd += ["--img-size", str(img_size)]
    for x, y in clip["pos"]:
        cmd += ["--pos", f"{x},{y}"]
    for x, y in clip["neg"]:
        cmd += ["--neg", f"{x},{y}"]

    log_path = os.path.join(RESULTS_DIR, "logs", f"{model}_{backend}_{img_size}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    masks, model_ms, wall_ms = [], [], []
    t_start = time.perf_counter()
    with open(log_path, "w") as log:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        msize = WORK_W * WORK_H
        try:
            for f in frames:
                t0 = time.perf_counter()
                p.stdin.write(memoryview(np.ascontiguousarray(f)).cast("B"))
                p.stdin.flush()
                buf = p.stdout.read(msize + 8)
                if len(buf) < msize + 8:
                    raise RuntimeError("rs_track exited early, see " + log_path)
                masks.append(np.frombuffer(buf[:msize], np.uint8).reshape(WORK_H, WORK_W))
                model_ms.append(float(np.frombuffer(buf[msize:], np.float64)[0]))
                wall_ms.append((time.perf_counter() - t0) * 1000)
            p.stdin.close()
        finally:
            _, status, ru = os.wait4(p.pid, 0)
            p.returncode = os.waitstatus_to_exitcode(status)
    total_s = time.perf_counter() - t_start
    with open(log_path) as f:
        log_text = f.read()
    device = "?"
    for line in log_text.splitlines():
        if "found device" in line or "GPU name" in line:
            device = line.split(":", 2)[-1].strip()
            break
    return dict(masks=masks, model_ms=model_ms, wall_ms=wall_ms, total_s=total_s,
                peak_rss_mb=ru.ru_maxrss / 1e6, device=device, log=log_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True)
    ap.add_argument("--model", nargs="+", required=True)
    ap.add_argument("--backend", nargs="+", default=["cpu"], choices=["cpu", "metal"])
    ap.add_argument("--img-size", nargs="+", type=int, default=[0])
    ap.add_argument("--contrast", nargs="+", default=["on"], choices=["on", "off"])
    ap.add_argument("--frames", type=int, default=0, help="0 = whole segment")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--overlay", action="store_true")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()

    clip = load_clips()[a.clip]
    count = a.frames or clip["count"]
    t0 = time.perf_counter()
    orig = read_frames(os.path.join(CLIPS_DIR, clip["file"]), clip["start"], count)
    decode_s = time.perf_counter() - t0
    lut, lo, hi = contrast_lut(orig)
    print(f"[{a.clip}] decoded {len(orig)} frames in {decode_s:.1f}s; contrast lo={lo:.0f} hi={hi:.0f}")
    adjusted = None

    out_dir = os.path.join(RESULTS_DIR, a.clip)
    os.makedirs(out_dir, exist_ok=True)
    for contrast in a.contrast:
        if contrast == "on" and adjusted is None:
            adjusted = apply_lut(orig, lut)
        frames = adjusted if contrast == "on" else orig
        for model in a.model:
            for backend in a.backend:
                for size in a.img_size:
                    name = f"{model}_{backend}_{size or 'def'}_c{contrast}{a.tag}"
                    print(f"  {name} ...", flush=True)
                    try:
                        r = run_one(frames, clip, model, backend, size, a.threads)
                    except Exception as e:
                        print(f"    FAILED: {e}")
                        append_result(dict(clip=a.clip, engine="sam3.cpp", model=model, backend=backend,
                                           img_size=size, contrast=contrast, frames=count, error=str(e)))
                        continue
                    ms = np.array(r["model_ms"])
                    track = ms[1:] if len(ms) > 1 else ms
                    cover = [float((m > 127).mean()) for m in r["masks"]]
                    row = dict(clip=a.clip, engine="sam3.cpp", model=model, backend=backend,
                               img_size=size, contrast=contrast, threads=a.threads, frames=len(ms),
                               first_ms=float(ms[0]), track_ms_mean=float(track.mean()),
                               track_ms_median=float(np.median(track)),
                               wall_ms_mean=float(np.mean(r["wall_ms"])), total_s=r["total_s"],
                               peak_rss_mb=r["peak_rss_mb"], device=r["device"],
                               empty_frames=int(sum(c < 1e-4 for c in cover)), name=name)
                    append_result(row)
                    print(f"    first {ms[0]:.0f} ms, track {track.mean():.0f} ms/frame "
                          f"(median {np.median(track):.0f}), total {r['total_s']:.1f}s, "
                          f"rss {r['peak_rss_mb']:.0f} MB, empty {row['empty_frames']}, dev {r['device']}")
                    save_masks(os.path.join(out_dir, name + ".npz"), r["masks"])
                    if a.overlay:
                        write_overlay(os.path.join(out_dir, name + ".mp4"), orig, r["masks"], 25,
                                      label=name, times_ms=r["model_ms"])


if __name__ == "__main__":
    main()
