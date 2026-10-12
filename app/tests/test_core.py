"""Headless end-to-end test: load -> click -> track both ways -> save -> reopen.

  python app/tests/test_core.py <video> <start> <end> x,y [x,y ...]
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rotoscope import config  # noqa: E402
from rotoscope.project import Project  # noqa: E402
from rotoscope.segmenter import Segmenter  # noqa: E402
from rotoscope.tracker import Tracker  # noqa: E402


def main():
    path, start, end = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    clicks = [tuple(float(v) for v in a.split(",")) for a in sys.argv[4:]]
    root = tempfile.mkdtemp(prefix="rotoscope_test_")
    settings = dict(config.load_settings(), root_dir=root)
    job = {"file": path, "start_frame": start, "end_frame": end, "project": "Test", "clip_name": os.path.basename(path),
           "result_path": os.path.join(root, "result.json")}
    t = time.perf_counter()
    p = Project(job, settings)
    p.load()
    print(f"loaded {p.count} frames {p.work_size} in {time.perf_counter() - t:.1f}s, range {p.start}-{p.end}")

    t = time.perf_counter()
    seg = Segmenter()
    trk = Tracker(settings["cutie_model"], settings["threads"])
    print(f"models loaded in {time.perf_counter() - t:.1f}s")

    mid = p.count // 2
    s = p.work_scale
    p.keyframes[mid] = {"pos": [[x * s, y * s] for x, y in clicks], "neg": [], "box": None}
    t = time.perf_counter()
    m = p.segment_keyframe(seg, mid)
    print(f"SAM first click {time.perf_counter() - t:.2f}s, coverage {(m > 127).mean():.3f}")
    t = time.perf_counter()
    p.segment_keyframe(seg, mid)
    print(f"SAM extra click {time.perf_counter() - t:.3f}s")

    for d in (-1, 1):
        t = time.perf_counter()
        n = p.track(trk, mid, d)
        dt = time.perf_counter() - t
        print(f"tracked {n} frames dir {d} in {dt:.1f}s ({dt / max(n, 1):.2f} s/frame)")
    print("missing:", p.missing_frames())

    t = time.perf_counter()
    out = p.save()
    print(f"saved {out} in {time.perf_counter() - t:.1f}s, {os.path.getsize(out) / 1e6:.1f} MB")

    q = Project(job, settings)
    q.load()
    print("restored:", q.restore(), "keyframes", list(q.keyframes), "missing", len(q.missing_frames()))
    print("ROOT", root)


if __name__ == "__main__":
    main()
