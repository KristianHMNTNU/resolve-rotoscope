"""Where things are saved: root folder layout, matte, session.json, result files.

  <root>/<Resolve project>/<clip>/matte_<start>-<end>/matte_<start>-<end>_000000.png ...
  <root>/<Resolve project>/<clip>/session.json

The matte is a PNG sequence: Fusion's Loader in Resolve cannot read movie files (tested in
Resolve 19.1.4: a ProRes .mov loads with length 0), and Resolve shows the sequence as one clip.
"""

import json
import logging
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import cv2
import numpy as np

log = logging.getLogger(__name__)

def sanitize(name):
    name = re.sub(r'[/:\\\x00-\x1f]', "_", name or "").strip(" .")
    return name or "Untitled"


def _secs(frames, fps):
    s = int(Fraction(frames) / Fraction(fps))
    return f"{s // 60:02d}m{s % 60:02d}s"


def clip_folder(root, project, clip_name, source_path):
    """Folder for this original file. Two different files with the same name get a suffix."""
    base = sanitize(os.path.splitext(clip_name)[0])
    parent = os.path.join(root, sanitize(project))
    n = 1
    while True:
        folder = os.path.join(parent, base if n == 1 else f"{base}_{n}")
        sess = read_session(folder)
        if sess is None or os.path.realpath(sess.get("source", "")) == os.path.realpath(source_path):
            return folder
        n += 1


def matte_name(session, start, end, fps):
    """Folder (and file prefix) for the matte of this range."""
    key = f"{start}-{end}"
    part = (session or {}).get("parts", {}).get(key)
    if part:
        return part["matte"]  # same range again: replace the old matte
    name = f"matte_{_secs(start, fps)}-{_secs(end + 1, fps)}"
    used = {p["matte"] for k, p in (session or {}).get("parts", {}).items() if k != key}
    if name in used:
        name = f"{name}_f{start}-{end}"
    return name


def sequence_paths(folder):
    """(first frame path, printf pattern) of the PNG sequence in `folder`."""
    base = os.path.basename(folder.rstrip("/"))
    return os.path.join(folder, f"{base}_000000.png"), os.path.join(folder, f"{base}_%06d.png")


def write_matte_sequence(folder, count, make_frame, progress=None, cancel=None, workers=4):
    """Write make_frame(i) (uint8 h x w) for i in range(count) as a PNG sequence into `folder`.
    Frames are made and written in parallel; the old folder is replaced only when all is done."""
    base = os.path.basename(folder.rstrip("/"))
    parent = os.path.dirname(folder.rstrip("/"))
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, f".{base}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)

    def one(i):
        if cancel and cancel():
            return False
        cv2.imwrite(os.path.join(tmp, f"{base}_{i:06d}.png"), make_frame(i), [cv2.IMWRITE_PNG_COMPRESSION, 3])
        return True

    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for ok in ex.map(one, range(count)):
            if not ok:
                break
            done += 1
            if progress:
                progress(done)
    if done < count:
        shutil.rmtree(tmp, ignore_errors=True)
        return False
    old = folder.rstrip("/") + ".old"
    if os.path.exists(folder):
        shutil.rmtree(old, ignore_errors=True)
        os.rename(folder, old)
    os.rename(tmp, folder)
    shutil.rmtree(old, ignore_errors=True)
    return True


def read_session(folder):
    try:
        with open(os.path.join(folder, "session.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_session(folder, session):
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, ".session.json.tmp")
    with open(tmp, "w") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)
    os.replace(tmp, os.path.join(folder, "session.json"))


def read_matte(folder, size):
    """Read a matte PNG sequence back as uint8 masks at `size` (used when reopening a session)."""
    w, h = size
    out = []
    for name in sorted(f for f in os.listdir(folder) if f.endswith(".png")):
        m = cv2.imread(os.path.join(folder, name), cv2.IMREAD_GRAYSCALE)
        out.append(cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA))
    return out


def _lua_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if v is None:
        return "nil"
    if isinstance(v, dict):
        return "{" + ", ".join(f"[{json.dumps(str(k), ensure_ascii=False)}] = {_lua_value(x)}"
                               for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(_lua_value(x) for x in v) + "}"
    # A JSON string without \u escapes is also a valid Lua 5.1 string literal.
    return json.dumps(str(v), ensure_ascii=False)


def write_result(job, result):
    """result.json for humans/debugging, result.lua for the Resolve script (no JSON parser in Lua)."""
    result = dict(result, written=time.strftime("%Y-%m-%d %H:%M:%S"))
    rj = job.get("result_path")
    if not rj:
        return
    os.makedirs(os.path.dirname(rj), exist_ok=True)
    lua_path = job.get("result_lua") or os.path.splitext(rj)[0] + ".lua"
    with open(lua_path + ".tmp", "w") as f:
        f.write("return " + _lua_value(result) + "\n")
    with open(rj + ".tmp", "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    os.replace(lua_path + ".tmp", lua_path)
    os.replace(rj + ".tmp", rj)   # result.json last: the Lua script waits for this one
