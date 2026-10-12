"""Click-to-mask on a single frame with SAM 2.1 (ONNX Runtime, CPU).

The image encoder runs once per frame (about 1.3 s here); every extra click only
runs the small decoder (a few tens of ms).
"""

import json
import os
import threading

import cv2
import numpy as np
import onnxruntime as ort

from .config import MODELS_DIR

SAM_DIR = os.path.join(MODELS_DIR, "onnx_sam21_tiny")


class Segmenter:
    def __init__(self, threads=4):
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        prov = ["CPUExecutionProvider"]
        self.enc = ort.InferenceSession(os.path.join(SAM_DIR, "vision_encoder.onnx"), so, providers=prov)
        self.dec = ort.InferenceSession(os.path.join(SAM_DIR, "mask_decoder.onnx"), so, providers=prov)
        c = json.load(open(os.path.join(SAM_DIR, "constants.json")))
        self.mean = np.array(c["image_mean"], np.float32) * 255
        self.std = np.array(c["image_std"], np.float32) * 255
        self._cache_key = None
        self._feats = None
        self._lock = threading.Lock()

    def _encode(self, key, rgb):
        if key != self._cache_key:
            x = cv2.resize(rgb, (1024, 1024), interpolation=cv2.INTER_AREA).astype(np.float32)
            x = ((x - self.mean) / self.std).transpose(2, 0, 1)[None]
            feats0, feats1, _feats2, feats2_no_mem, _pos = self.enc.run(None, {"pixel_values": x})
            self._feats = (feats0, feats1, feats2_no_mem)
            self._cache_key = key
        return self._feats

    def segment(self, key, rgb, pos, neg, box=None):
        """Return a uint8 mask (0..255, soft edges) at the size of `rgb`.

        key: anything identifying the frame (encoder output is cached per key).
        pos/neg: lists of (x, y) in `rgb` pixel coordinates. box: (x0, y0, x1, y1) or None.
        """
        h, w = rgb.shape[:2]
        with self._lock:
            feats0, feats1, feats2 = self._encode(key, rgb)
            pts, lbl = [], []
            for (x, y), l in [(p, 1) for p in pos] + [(p, 0) for p in neg]:
                pts.append((x * 1024 / w, y * 1024 / h))
                lbl.append(l)
            if box is not None:
                x0, y0, x1, y1 = box
                pts += [(x0 * 1024 / w, y0 * 1024 / h), (x1 * 1024 / w, y1 * 1024 / h)]
                lbl += [2, 3]
            if not pts:
                return np.zeros((h, w), np.uint8)
            low, *_ = self.dec.run(None, {
                "feats0": feats0, "feats1": feats1, "feats2_cond": feats2,
                "input_points": np.array(pts, np.float32)[None, None],
                "input_labels": np.array(lbl, np.int32)[None, None]})
        logits = cv2.resize(low[0, 0], (w, h), interpolation=cv2.INTER_LINEAR)
        prob = 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))
        return (prob * 255).astype(np.uint8)
