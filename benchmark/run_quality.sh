#!/bin/zsh
# Full-length quality runs with overlay videos (run with nice, takes a couple of hours).
# Main clips: 0248 0256 0255. Extra: 0281 (hands) and 0236 (short).
cd "${0:A:h}"
PY="$HOME/Library/Application Support/Rotoscope/venv-bench/bin/python"
PRIMARY=(0248 0256 0255)
ALL=(0248 0256 0255 0281 0236)

# Chosen setup: Cutie base at 480 px short side (853 long side), with and without contrast.
for c in $ALL; do
  "$PY" bench_cutie.py --clip $c --model base --size 853 --contrast on off --threads 8 --overlay
done
# Fast alternative: Cutie small at 512.
for c in $ALL; do
  "$PY" bench_cutie.py --clip $c --model small --size 512 --contrast on --threads 8 --overlay
done
# References: EdgeTAM (sam3.cpp) at 512 and SAM 2.1 tiny (ONNX) at 1024 on the primary clips.
for c in $PRIMARY; do
  "$PY" bench_sam3.py --clip $c --model edgetam_f16 --backend cpu --img-size 512 --contrast on --overlay
done
for c in $PRIMARY; do
  "$PY" bench_onnx.py --clip $c --provider cpu --contrast on --overlay
done
echo QUALITY_DONE
