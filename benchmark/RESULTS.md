# Benchmark

Which tracking models are fast and good enough on the machine this was built for:
a 2017 iMac with an Intel Core i7-7700K (4 cores), 64 GB RAM and an AMD Radeon Pro 580, running macOS 13.

## Summary

* **The GPU can't be used.**
  * sam3.cpp (ggml) needs Metal features the Radeon Pro 580 doesn't have, for both matrix multiplication and attention. With the check forced off, the EdgeTAM encoder went from 1.42 s to 1.31 s, so there is nothing to gain.
  * ONNX Runtime with CoreML was twice as slow as the CPU and used 17 GB of RAM.
* **Everything runs on the CPU.** Cutie with 8 threads is about 10 % faster than with 4. ONNX Runtime and sam3.cpp don't gain from 8 threads.
* **Cutie is the only model that is clearly fast enough.**
* **The contrast stretch for D-Log footage changed little.** On clip 0248 the masks with and without it overlap 98 %. It is kept on because it doesn't hurt.

## Speed

Clip 0255 (4K D-Log, a person walking away), 50 frames, contrast stretch on.
"Internal size" is the longest side the model works at.

| Model | Internal size | s/frame | 20 s clip |
|---|---|---|---|
| Cutie small, 8 threads | 512 | 0.28 | 2.5 min |
| Cutie small | 512 | 0.34 | 3 min |
| Cutie base, 8 threads | 512 | 0.39 | 3 min |
| **Cutie base, 8 threads (used)** | **853** | **~0.55** | **~5 min** |
| Cutie small | 768 | 0.57 | 5 min |
| Cutie base, 8 threads | 768 | 0.72 | 6 min |
| EdgeTAM (sam3.cpp) | 512 | 0.87 | 7 min |
| Cutie small | 1024 | 0.99 | 8 min |
| Cutie base | 1024 | 1.41 | 12 min |
| SAM 2.1 tiny (sam3.cpp) | 512 | 1.6–2.0 | 15 min |
| EdgeTAM (sam3.cpp) | 768 | 1.93 | 16 min |
| SAM 2.1 small (sam3.cpp) | 512 | 2.19 | 18 min |
| EdgeTAM (sam3.cpp) | 1024 | 3.41 | 28 min |
| SAM 2.1 tiny (ONNX Runtime) | 1024 (fixed) | 3.71 | 31 min |
| SAM 2.1 tiny (sam3.cpp) | 768 | 5.16 | 43 min |
| SAM 2.1 small (sam3.cpp) | 768 | 5.64 | 47 min |
| SAM 2.1 tiny / small (sam3.cpp) | 1024 | ~7 / ~11 | 1–1.5 h |

The last row was measured while the machine was busy with other work. SAM 2.1 tiny took about 7 s/frame on an idle machine.

**Memory:**
* sam3.cpp uses 0.4–1.3 GB.
* Cutie and ONNX Runtime use 2–4.5 GB.
* With 500 frames at 1080p held in memory, the whole app uses about 7–8 GB.

**Clicking (SAM 2.1 tiny, ONNX Runtime):**
* The first click on a frame takes 1.3–2 s; every click after that takes under 0.1 s.
* One box, or three clicks, selects a whole person.

## Choice

The app uses **Cutie base** for tracking, at 480 px on the short side (Cutie's default), and **SAM 2.1 tiny** for turning clicks into a mask. Cutie small and higher tracking resolutions can be picked in the app's settings.

SAM 2.1 and EdgeTAM track better on paper, and would be the choice on an Apple Silicon Mac, but are too slow here. Most of their time goes to memory attention: about 2 of the 3.7 s per frame for SAM 2.1 tiny.

## Quality

Overlay videos are written to `results/<clip>/` (matte on the left, cut-out on a checkerboard on the right). They are not in the repository.

| Clip | What happens | Result |
|---|---|---|
| 0256 | Person turns and walks right up to the camera | Tracked the whole way, including when very close |
| 0248 | Person in a landscape | The backpack drops out near the end after the person turns; fixed with one click |

## Reproducing

**Clips.** The test clips are not in the repository. `clips.json` lists the segments and click points; the scripts look for the files one folder up.

**Python environment.**
```bash
uv venv --python 3.11 "$HOME/Library/Application Support/Rotoscope/venv-bench"
uv pip install --python "$HOME/Library/Application Support/Rotoscope/venv-bench/bin/python" \
  torch==2.2.2 torchvision==0.17.2 "numpy<2" av opencv-python-headless onnxruntime psutil hydra-core einops
```

**sam3.cpp.**
1. Clone [sam3.cpp](https://github.com/PABannier/sam3.cpp) into `~/Library/Application Support/Rotoscope/vendor` and build it with CMake.
2. With the macOS 13 SDK, the `mathMode` / `mathFloatingPointFunctions` lines in `ggml-metal-device.m` need an `#if __MAC_OS_X_VERSION_MAX_ALLOWED >= 150000` guard.
3. Build `sam3_driver/rs_track.cpp` against `libsam3.a` and the ggml libraries, and put the binary in `~/Library/Application Support/Rotoscope/bin`.

**Models.**
* ggml weights from [PABannier/sam3.cpp](https://huggingface.co/PABannier/sam3.cpp).
* Cutie weights from the [Cutie releases](https://github.com/hkchengrex/Cutie/releases).
* SAM 2.1 ONNX files from [square-zero-labs/sam2.1-tiny-video-onnx](https://huggingface.co/square-zero-labs/sam2.1-tiny-video-onnx).

**Running.**
* `run_speed.sh` measures speed.
* `run_quality.sh` makes the overlay videos (takes hours).
* `summarize.py` prints the table.
* `compare.py` makes side-by-side contact sheets.

| File | What it does |
|---|---|
| `bench_cutie.py` | Cutie (PyTorch, CPU) |
| `bench_onnx.py` | SAM 2.1 tiny video tracking with ONNX Runtime; builds the memory bank in Python |
| `bench_sam3.py` | sam3.cpp through `rs_track` |
| `common.py` | frame reading, contrast stretch, overlay videos |
| `results/results.jsonl` | all measurements |
