"""Reading frames straight from the original file (no export from Resolve)."""

import logging
from dataclasses import dataclass
from fractions import Fraction

import av
import numpy as np

log = logging.getLogger(__name__)

# Frames are kept in memory at this size (longest side). The models work on
# smaller copies anyway; the matte is scaled up to full resolution on export.
WORK_LONG_SIDE = 1920


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: Fraction
    frames: int
    timecode: str

    @property
    def work_size(self):
        s = min(1.0, WORK_LONG_SIDE / max(self.width, self.height))
        return (int(round(self.width * s / 2)) * 2, int(round(self.height * s / 2)) * 2)


def probe(path):
    with av.open(path) as c:
        v = c.streams.video[0]
        fps = v.average_rate or v.guessed_rate
        n = v.frames
        if not n and v.duration:
            n = int(round(float(v.duration * v.time_base) * fps))
        tc = v.metadata.get("timecode") or c.metadata.get("timecode") or ""
        return VideoInfo(path, v.codec_context.width, v.codec_context.height, Fraction(fps), int(n), tc)


def timecode_to_frames(tc, fps):
    """'HH:MM:SS:FF' (or ';' for drop frame) to a frame count."""
    if not tc:
        return 0
    drop = ";" in tc
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    nominal = int(round(float(fps)))
    total = ((h * 60 + m) * 60 + s) * nominal + f
    if drop:
        dropped = 2 if nominal == 30 else 4
        minutes = h * 60 + m
        total -= dropped * (minutes - minutes // 10)
    return total


def read_frames(info, start, count, size=None, progress=None, cancel=None):
    """Decode frames [start, start+count) as RGB24 arrays at `size` (default work size).

    Frame numbers count from the first frame in the file. We seek to the keyframe
    before `start` and decode forward, so the result is frame accurate.
    """
    w, h = size or info.work_size
    frames = []
    with av.open(info.path) as c:
        v = c.streams.video[0]
        v.thread_type = "AUTO"
        tb, rate = v.time_base, info.fps
        first_pts = v.start_time or 0
        if start > 0:
            c.seek(first_pts + int(Fraction(start) / rate / tb), stream=v, backward=True)
        for fr in c.decode(v):
            if fr.pts is None:
                continue
            idx = int(round((fr.pts - first_pts) * tb * rate))
            if idx < start:
                continue
            frames.append(fr.reformat(width=w, height=h, format="rgb24", interpolation="AREA").to_ndarray())
            if progress:
                progress(len(frames), count)
            if len(frames) == count or (cancel and cancel()):
                break
    if len(frames) < count:
        log.warning("only got %d of %d frames from %s", len(frames), count, info.path)
    return frames
