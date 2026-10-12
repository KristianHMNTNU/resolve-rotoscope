# Rotoscope for DaVinci Resolve

Cut people and objects out of your footage in DaVinci Resolve 19 (free version).

1. Click on what you want to keep.
2. Let it track through the clip, and fix the frames where it goes wrong.
3. Press Done. The cut-out ends up on the clip in your timeline, with a transparent background.

Tested on an Intel iMac with macOS 13 and Resolve 19.1.4. Everything runs on the CPU.

## How it works

* **Masking a frame:** SAM 2.1 turns your clicks into a mask.
* **Tracking:** [Cutie](https://github.com/hkchengrex/Cutie) follows the mask through the clip.
* **Reading frames:** the app reads frames straight from the original file, so nothing has to be rendered out of Resolve first.
* **Flat footage:** the models get a contrast-stretched copy of each frame, which helps with log footage like D-Log. The matte itself always applies to the original.
* **Saving:** the matte is written as a black-and-white PNG sequence at full resolution.
* **In Resolve:** a Lua script starts the app and waits for it. Afterwards it imports the matte and adds a Fusion composition to the clip: `MediaIn` + `Loader` (matte) → `MatteControl` → `MediaOut`.

Speed on the test machine: about 0.55 s per frame, so a 20-second 4K clip takes around 5 minutes to track. See [benchmark/RESULTS.md](benchmark/RESULTS.md) for how the models were chosen.

## Requirements

* macOS 13 or later (tested on Intel; Apple Silicon should work but is untested)
* DaVinci Resolve 19
* About 3 GB of disk space
* An internet connection during installation

## Installation

```bash
./install.sh
```

Then restart DaVinci Resolve. You'll find two new entries under **Workspace > Scripts**:
* **Rotoscope**
* **Rotoscope - Apply Last Matte**

The first time the app opens a clip, macOS may ask whether Rotoscope can access your Desktop folder or an external drive. Allow it, or the app can't read the footage.

The installer puts things in these places, and nothing else on the system is changed:

| What | Where |
|---|---|
| App, Python environment, models, settings | `~/Library/Application Support/Rotoscope/` |
| Resolve scripts | `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/` |
| Saved mattes | `~/Movies/Rotoscope/` (can be changed in the app's settings) |

## Usage

1. On the **Edit** or **Cut** page, put the playhead over the clip you want to cut out.
2. Choose **Workspace > Scripts > Rotoscope**. The Rotoscope window opens with the clip loaded.
3. **Select the object** on a frame where it is clearly visible:
   * **Click** marks part of the object; **right-click** marks something that is not.
   * **Shift + drag** draws a box around it. This is often the quickest way.
   * A single click often selects only part of a person, so click on the head, body and legs.
4. **Track** with **◀ Both ▶**, **Forward ▶** or **◀ Backward**.
5. **Step through the result.** Where the matte is wrong, click on that frame to fix it, then track on from there. Tracking always stops at the next frame you clicked on, so your fixes are kept.
6. **Adjust the edges if needed:**
   * **Feather:** softens the edge.
   * **Shrink / grow:** moves the edge in or out. A small negative value removes bright halos.
   * **Temporal smoothing:** reduces flicker.
7. Press **Done**. Back in Resolve, the clip now has a transparent background.

To put the person in front of something else, place the background on V1 and the cut-out clip on V2. For text behind a person, put the original on V1, the text on V2 and the cut-out on V3.

Closing the window without pressing **Done** leaves the project untouched.

### Keyboard

| Key | Action |
|---|---|
| ← / → | previous / next frame |
| Shift + ← / → | 10 frames back / forward |
| Home / End | first / last frame |
| Space | play / pause |
| Cmd + Z | undo the last click |

### Going back to a clip

Run Rotoscope on the same part of the same clip again, and it picks up where you left off: your clicks, edge settings and tracked matte are loaded again. When you press Done, the old matte and the Fusion composition are replaced.

### If something fails after Done

The matte is saved before anything happens in Resolve. Choose **Workspace > Scripts > Rotoscope - Apply Last Matte** to add it to the clip without doing the work again.

### Removing a cut-out

The original clip is never changed. To remove the cut-out:
* **The Fusion composition:** on the Fusion page, right-click the clip and delete the composition named "Rotoscope".
* **The matte:** delete it from the "Rotoscope" bin in the media pool.

## Where mattes are saved

```
~/Movies/Rotoscope/
  <Resolve project>/
    <clip name>/
      matte_00m12s-00m25s/     PNG sequence, one image per frame
      session.json             clicks, settings and the path to the source file
```

* **Each part of a clip** you work on gets its own matte.
* **The same part again** replaces the old matte.

## Limitations

* **One object at a time.**
* **No speed changes.** Clips with retime, speed ramps, or a frame rate that differs from the timeline are not supported. The script tells you and stops.
* **Edges, not hair matting.** Edges are clean and soft, but fine hair and motion blur are not matted in detail.

## Troubleshooting

| Log | Path |
|---|---|
| Resolve script | `~/Library/Application Support/Rotoscope/logs/resolve_script.log` |
| App | `~/Library/Application Support/Rotoscope/logs/app.log` |

The app can also be started without Resolve:

```bash
"$HOME/Library/Application Support/Rotoscope/Rotoscope.app/Contents/MacOS/rotoscope" --file /path/to/clip.mp4
```

## Development

```
app/rotoscope/   the app (Python, PySide6)
app/tests/       tests: core pipeline, GUI (offscreen) and the Resolve script against a mock Resolve API
resolve/         the Resolve scripts (Lua)
benchmark/       model speed and quality tests
install.sh       installer
```

```bash
PY="$HOME/Library/Application Support/Rotoscope/venv/bin/python"
"$PY" app/tests/test_lua.py                          # needs: pip install lupa
"$PY" app/tests/test_core.py clip.mp4 0 99 1534,850  # load, click, track, save, reopen
```

## Credits

* [Cutie](https://github.com/hkchengrex/Cutie) by Ho Kei Cheng et al. (MIT) for tracking.
* [SAM 2.1](https://github.com/facebookresearch/sam2) by Meta (Apache 2.0) for clicks, using the ONNX export from [square-zero-labs/sam2.1-tiny-video-onnx](https://huggingface.co/square-zero-labs/sam2.1-tiny-video-onnx).
* The models are downloaded by the installer and are not part of this repository.
