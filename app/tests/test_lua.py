"""Run resolve/Rotoscope.lua against the fake Resolve API (Lua 5.1 via lupa), for several scenarios.

  python app/tests/test_lua.py
"""

import json
import os
import sys
import tempfile

import lupa.lua51 as lupa

# Run against a temporary HOME so the real jobs/ and logs/ folders are never touched.
FAKE_HOME = tempfile.mkdtemp(prefix="rotoscope_lua_home_")
os.environ["HOME"] = FAKE_HOME
_app = os.path.join(FAKE_HOME, "Library/Application Support/Rotoscope")
os.makedirs(os.path.join(_app, "Rotoscope.app/Contents/MacOS"))
open(os.path.join(_app, "Rotoscope.app/Contents/MacOS/rotoscope"), "w").close()

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from rotoscope import storage  # noqa: E402

SCRIPT = os.path.join(HERE, "..", "..", "resolve", "Rotoscope.lua")
MOCK = os.path.join(HERE, "mock_resolve.lua")
JOB = os.path.join(_app, "jobs", "job.json")

BASE = dict(clip_name="DJI_20260815170443_0255_D.MP4", file="/Volumes/Media/DJI_20260815170443_0255_D.MP4",
            project="Summer", src_start=1500, duration=500, src_span=499, item_start=90100,
            playhead_tc="01:00:10:00", timeline_fps="25", clip_fps="25.000", current_item=True)


def run(name, app_result=None, mode=None, **overrides):
    mock = dict(BASE, **overrides)
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    g = lua.globals()
    g.ROTOSCOPE_MODE = mode
    g.MOCK = lua.table_from(mock)
    state = {"opened": 0, "alerts": []}

    def simulate_app():
        state["opened"] += 1
        job = json.load(open(JOB))
        state["job"] = job
        if app_result is not None:
            storage.write_result(job, app_result(job))

    g.py_simulate = simulate_app
    g.py_alert = lambda cmd: state["alerts"].append(cmd)
    lua.execute("""
        local real = os.execute
        os.execute = function(cmd)
            if cmd:find("^open %-n") then py_simulate() return 0 end
            if cmd:find("^sleep") or cmd:find("pgrep") then return 0 end
            if cmd:find("osascript") then py_alert(cmd) return 0 end
            return real(cmd)
        end
    """)
    lua.execute(open(MOCK).read())
    lua.execute(open(SCRIPT).read())
    calls = list(g.MOCK_CALLS.values())
    state["active_is_cutout"] = bool(g.MOCK_ACTIVE_IS_CUTOUT())
    print(f"\n=== {name}: app opened {state['opened']}x, alerts {len(state['alerts'])}")
    for a in state["alerts"]:
        print("   alert:", a[a.find("message"):][:160])
    if "job" in state:
        j = state["job"]
        print(f"   job: frames {j['start_frame']}-{j['end_frame']} fps {j['fps']} project {j['project']} clip {j['clip_name']}")
    for c in calls:
        print("   call:", c)
    return state, calls


def done(job):
    d = "/Users/me/Movies/Rotoscope/Summer/DJI_0255/matte_01m00s-01m20s"
    return {"status": "done", "matte": d + "/matte_01m00s-01m20s_000000.png", "matte_dir": d,
            "matte_pattern": d + "/matte_01m00s-01m20s_%06d.png",
            "start_frame": job["start_frame"], "end_frame": job["end_frame"],
            "frames": job["end_frame"] - job["start_frame"] + 1, "fps": "25", "width": 3840, "height": 2160}


if __name__ == "__main__":
    s, c = run("normal", done)
    assert s["active_is_cutout"]
    assert s["job"]["start_frame"] == 1500 and s["job"]["end_frame"] == 1999
    assert any("ImportMedia" in x and "into Rotoscope" in x for x in c)
    assert any("RotoscopeCutout.Background <- MediaIn1" in x for x in c)
    assert any("RotoscopeCutout.Foreground <- Loader" in x for x in c)
    assert any("MediaOut1.Input <- MatteControl" in x for x in c)
    assert any("MatteCombine 1" in x for x in c) and any("CombineOp 0" in x for x in c)
    assert any("GlobalOut 499" in x for x in c)

    s, c = run("Resolve() is nil, global resolve exists (free version)", done, like_free_menu_script=True)
    assert s["opened"] == 1 and any("ImportMedia" in x for x in c)

    s, c = run("lists with __flags and {name = index} shapes", done, weird_lists=True,
               like_free_menu_script=True, bin_exists=True)
    assert any("ImportMedia" in x for x in c) and any("MediaOut1.Input <- MatteControl" in x for x in c)

    s, c = run("only the *List() API: bin is created", done, weird_lists=True,
               no_old_api=True, bin_exists=True, current_item=True)
    assert any("ImportMedia" in x for x in c) and any("MediaOut1.Input <- MatteControl" in x for x in c)

    s, c = run("trimmed clip: GetSourceStartFrame one frame early", done,
               src_start=131, src_span=363, duration=363, left_offset=132)
    assert s["job"]["start_frame"] == 132 and s["job"]["end_frame"] == 494, s["job"]

    s, c = run("apply last matte", None, mode="apply_last",
               weird_lists=True, like_free_menu_script=True, src_start=131, src_span=363, duration=363, left_offset=132)
    assert s["opened"] == 0 and any("ImportMedia" in x for x in c) and any("RenameComp" in x for x in c)

    s, c = run("exclusive end frame", done, src_span=500)
    assert s["opened"] == 1

    s, c = run("retimed clip", done, src_span=999)
    assert s["opened"] == 0 and s["alerts"]

    s, c = run("different fps than timeline", done, clip_fps="50")
    assert s["opened"] == 0 and s["alerts"]

    s, c = run("title or generator", done, no_media=True)
    assert s["opened"] == 0 and s["alerts"]

    s, c = run("app closed without Done", lambda j: {"status": "cancelled"})
    assert s["opened"] == 1 and not any("ImportMedia" in x for x in c)

    s, c = run("rerun replaces our nodes in the same composition", done, existing_comp=True)
    assert any("DeleteTool RotoscopeMatte" in x for x in c) and any("DeleteTool RotoscopeCutout" in x for x in c)
    assert not any("AddFusionComp" in x for x in c) and any("MediaOut1.Input <- MatteControl" in x for x in c)

    s, c = run("leftover composition with our nodes is removed", done, mode="apply_last",
               leftover_comp=True, src_start=0, src_span=131, duration=132)
    assert any("DeleteComp Composition 2 true" in x for x in c), c
    assert any("MediaOut1.Input <- MatteControl" in x for x in c) and not any("AddFusionComp" in x for x in c)

    s, c = run("Fusion cannot read the matte: old nodes kept", done, existing_comp=True,
               loader_fails=True)
    assert s["alerts"] and not any("DeleteTool RotoscopeMatte" in x for x in c)
    assert any("DeleteTool Loader" in x for x in c)

    s, c = run("two compositions with the same name", None, mode="apply_last",
               duplicate_names=True, src_start=0, src_span=131, duration=132)
    assert s["active_is_cutout"], "the composition the timeline renders must get the cut-out"

    s, c = run("matte imported as a PNG sequence", done)
    assert any("ImportMedia" in x and "_%06d.png [0-499]" in x for x in c), c

    s, c = run("new clip gets a Rotoscope composition", done)
    assert any("AddFusionComp Composition 1" in x for x in c) and any("RenameComp Composition 1 Rotoscope true" in x for x in c)

    s, c = run("no current item: found on a track", done, current_item=False)
    assert s["opened"] == 1

    s, c = run("playhead outside any clip", done, current_item=False, playhead_tc="02:00:00:00")
    assert s["opened"] == 0 and s["alerts"]
    print("\nALL LUA SCENARIOS OK")
