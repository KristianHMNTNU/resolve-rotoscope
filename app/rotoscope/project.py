"""Editing state for one clip range, independent of the GUI (so it can be tested headless)."""

import datetime
import hashlib
import logging
import os
import time
from fractions import Fraction

import numpy as np

from . import contrast, edges, storage, video
from .config import CACHE_DIR

log = logging.getLogger(__name__)


class Project:
    def __init__(self, job, settings):
        self.job = job
        self.settings = settings
        self.info = video.probe(job["file"])
        # The matte must have exactly the frame rate of the original file.
        self.fps = self.info.fps or Fraction(job.get("fps") or 25).limit_denominator(1001)
        self.start, self.end = self._resolve_range(job)
        self.count = self.end - self.start + 1
        self.frames = []
        self.lut = None
        self.probs = []        # per frame: uint8 soft mask at work size, or None
        self.keyframes = {}    # frame -> {"pos": [...], "neg": [...], "box": [x0,y0,x1,y1] | None}
        self.edge = {"feather": 2.0, "grow": -2.0, "smooth": 0.3}
        self.dirty = False

    # ── setup ──────────────────────────────────────────────────────────────

    def _resolve_range(self, job):
        n = self.info.frames
        start = int(job.get("start_frame", 0))
        end = int(job.get("end_frame", n - 1))
        if start >= n and self.info.timecode:
            # Resolve gave frame numbers on the source timecode, not from 0.
            off = video.timecode_to_frames(self.info.timecode, self.info.fps)
            log.info("range %d-%d looks timecode based, subtracting %d", start, end, off)
            start, end = start - off, end - off
        start = max(0, min(start, n - 1))
        end = max(start, min(end, n - 1))
        return start, end

    def load(self, progress=None, cancel=None):
        self.frames = video.read_frames(self.info, self.start, self.count, progress=progress, cancel=cancel)
        self.count = len(self.frames)
        self.end = self.start + self.count - 1
        self.lut = contrast.make_lut(self.frames)
        self.probs = [None] * self.count

    @property
    def work_size(self):
        h, w = self.frames[0].shape[:2]
        return w, h

    @property
    def work_scale(self):
        return self.work_size[0] / self.info.width

    # ── storage locations ───────────────────────────────────────────────────

    def folder(self):
        return storage.clip_folder(self.settings["root_dir"], self.job.get("project", "No project"),
                                   self.job.get("clip_name") or os.path.basename(self.info.path), self.info.path)

    def matte_path(self):
        folder = self.folder()
        return os.path.join(folder, storage.matte_name(storage.read_session(folder), self.start, self.end, self.fps))

    # ── clicks and masks ─────────────────────────────────────────────────────

    def model_frame(self, i):
        return contrast.apply(self.frames[i], self.lut)

    def segment_keyframe(self, segmenter, i):
        kf = self.keyframes.get(i)
        if not kf or (not kf["pos"] and not kf.get("box")):
            self.keyframes.pop(i, None)
            return None
        m = segmenter.segment(("frame", i), self.model_frame(i), kf["pos"], kf["neg"], kf.get("box"))
        self.probs[i] = m
        self.dirty = True
        return m

    def track(self, tracker, seed, direction, progress=None, cancel=None):
        """Track from `seed` forwards (+1) or backwards (-1) until the clip ends or the
        next frame with clicks. Calls progress(done, total, sec_per_frame)."""
        if self.probs[seed] is None:
            raise ValueError("There is no matte on this frame. Click on the object first.")
        stop = -1 if direction < 0 else self.count
        for k in self.keyframes:
            if k != seed and (k - seed) * direction > 0:
                stop = max(stop, k) if direction < 0 else min(stop, k)
        indices = list(range(seed + direction, stop, direction))
        if not indices:
            return 0
        done = 0
        for i, p in tracker.track(self.frames, self.lut, seed, self.probs[seed], indices,
                                  self.settings["internal_size"], progress=progress, cancel=cancel):
            self.probs[i] = p
            done += 1
        self.dirty = True
        return done

    # ── output ─────────────────────────────────────────────────────────────

    def matte_at(self, i, size, scale):
        p = edges.smooth_in_time(self.probs, i, self.edge["smooth"])
        if p is None:
            return np.zeros((size[1], size[0]), np.uint8)
        return edges.finish(p, size, scale, self.edge["grow"], self.edge["feather"])

    def missing_frames(self):
        return [i for i, p in enumerate(self.probs) if p is None]

    def save(self, progress=None, cancel=None):
        """Write the matte (PNG sequence) + session.json. Returns the matte folder, or None if cancelled."""
        folder = self.folder()
        path = self.matte_path()
        size = (self.info.width, self.info.height)
        ok = storage.write_matte_sequence(path, self.count, lambda i: self.matte_at(i, size, 1.0),
                                          progress=progress, cancel=cancel)
        if not ok:
            return None
        session = storage.read_session(folder) or {}
        session.update(source=self.info.path, clip_name=self.job.get("clip_name"),
                       project=self.job.get("project"))
        parts = session.setdefault("parts", {})
        parts[f"{self.start}-{self.end}"] = {
            "matte": os.path.basename(path),
            "start_frame": self.start, "end_frame": self.end, "frames": self.count,
            "fps": str(self.fps), "width": self.info.width, "height": self.info.height,
            "model": f"cutie-{self.settings['cutie_model']}+sam2.1-tiny",
            "internal_size": self.settings["internal_size"],
            "edges": dict(self.edge),
            "clicks": {str(k): v for k, v in sorted(self.clicks_in_source_pixels().items())},
            "click_coordinates": "pixels of the original file",
            "date": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        storage.write_session(folder, session)
        self._save_mask_cache()
        self.dirty = False
        return path

    def restore(self):
        """Reopen an earlier session for this range: clicks, edge settings and masks."""
        folder = self.folder()
        session = storage.read_session(folder)
        part = (session or {}).get("parts", {}).get(f"{self.start}-{self.end}")
        if not part:
            return False
        s = self.work_scale
        for k, v in part.get("clicks", {}).items():
            self.keyframes[int(k)] = {
                "pos": [[x * s, y * s] for x, y in v.get("pos", [])],
                "neg": [[x * s, y * s] for x, y in v.get("neg", [])],
                "box": [c * s for c in v["box"]] if v.get("box") else None}
        self.edge.update(part.get("edges", {}))
        if self._load_mask_cache():
            return True
        # No cache: fall back to the exported matte (edge settings already baked in).
        path = os.path.join(folder, part["matte"])
        if os.path.isdir(path):
            for i, m in enumerate(storage.read_matte(path, self.work_size)[: self.count]):
                self.probs[i] = m
            self.edge.update(grow=0.0, feather=0.0)
        return True

    # Hidden cache with the raw tracked masks, so a reopened session can be
    # edited further without baking the edge settings in twice.
    def _cache_path(self):
        key = hashlib.sha1(f"{os.path.realpath(self.info.path)}|{self.start}|{self.end}".encode()).hexdigest()
        return os.path.join(CACHE_DIR, f"masks_{key}.npz")

    def _save_mask_cache(self):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            h, w = self.frames[0].shape[:2]
            have = np.array([p is not None for p in self.probs])
            bits = np.stack([(p if p is not None else np.zeros((h, w), np.uint8)) >= 128 for p in self.probs])
            np.savez_compressed(self._cache_path(), bits=np.packbits(bits, axis=-1), have=have,
                                shape=np.array(bits.shape))
        except Exception:
            log.exception("could not write mask cache")

    def _load_mask_cache(self):
        try:
            d = np.load(self._cache_path())
            n, h, w = d["shape"]
            if (w, h) != self.work_size or n != self.count:
                return False
            bits = np.unpackbits(d["bits"], axis=-1)[:, :, :w]
            for i in range(n):
                self.probs[i] = bits[i] * np.uint8(255) if d["have"][i] else None
            return True
        except (OSError, KeyError, ValueError):
            return False

    def clicks_in_source_pixels(self):
        s = 1.0 / self.work_scale
        return {k: {"pos": [[x * s, y * s] for x, y in v["pos"]],
                    "neg": [[x * s, y * s] for x, y in v["neg"]],
                    "box": [c * s for c in v["box"]] if v.get("box") else None}
                for k, v in self.keyframes.items()}
