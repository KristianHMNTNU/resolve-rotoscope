"""Contact sheet: the same frames from several runs side by side (rows = runs, columns = frames).

Example:
  python compare.py --clip 0255 --runs edgetam_f16_cpu_def_con cutie-base_cpu_768_con \
      --frames 0 100 250 499 --out sheet.jpg
"""

import argparse
import os

import cv2
import numpy as np

from common import CLIPS_DIR, RESULTS_DIR, load_clips, load_masks, read_frames


def tile(frame, mask, label, size):
    w, h = size
    img = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
    mk = cv2.resize(mask.astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_LINEAR) > 127
    img[mk] = (0.55 * img[mk] + 0.45 * np.array([255, 40, 40])).astype(np.uint8)
    cnts, _ = cv2.findContours(mk.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(img, cnts, -1, (255, 255, 0), 1)
    cv2.putText(img, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
    cv2.putText(img, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True)
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--frames", nargs="+", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=480)
    ap.add_argument("--crop", type=int, nargs=4, default=None, help="x0 y0 x1 y1 in 1080p coords")
    a = ap.parse_args()

    clip = load_clips()[a.clip]
    n = max(a.frames) + 1
    frames = read_frames(os.path.join(CLIPS_DIR, clip["file"]), clip["start"], n)
    rows = []
    for run in a.runs:
        masks = load_masks(os.path.join(RESULTS_DIR, a.clip, run + ".npz"))
        tiles = []
        for i in a.frames:
            f, m = frames[i], masks[i] if i < len(masks) else np.zeros(frames[i].shape[:2], bool)
            if a.crop:
                x0, y0, x1, y1 = a.crop
                f, m = f[y0:y1, x0:x1], m[y0:y1, x0:x1]
            h = round(a.width * f.shape[0] / f.shape[1])
            tiles.append(tile(f, m, f"{run[:38]} f{i}", (a.width, h)))
        rows.append(np.hstack(tiles))
    cv2.imwrite(a.out, cv2.cvtColor(np.vstack(rows), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])


if __name__ == "__main__":
    main()
