#!/bin/zsh
# Speed matrix: 50 frames of clip 0255 (contrast on), all candidates and internal sizes.
cd "${0:A:h}"
PY="$HOME/Library/Application Support/Rotoscope/venv-bench/bin/python"
N=50
"$PY" bench_cutie.py --clip 0255 --model small base --size 512 768 1024 --frames $N --tag _speed
"$PY" bench_cutie.py --clip 0255 --model small base --size 512 768 --frames $N --threads 8 --tag _speed_t8
"$PY" bench_onnx.py --clip 0255 --provider cpu --frames $N --tag _speed
"$PY" bench_onnx.py --clip 0255 --provider cpu --frames $N --threads 8 --tag _speed_t8
"$PY" bench_sam3.py --clip 0255 --model edgetam_f16 sam2.1_hiera_tiny_f16 sam2.1_hiera_small_f16 \
    --backend cpu --img-size 512 768 1024 --frames $N --tag _speed
"$PY" bench_sam3.py --clip 0255 --model edgetam_q8_0 sam2.1_hiera_tiny_q8_0 \
    --backend cpu --img-size 512 --frames $N --tag _speed
"$PY" bench_sam3.py --clip 0255 --model edgetam_f16 --backend cpu --img-size 512 --frames $N --threads 8 --tag _speed_t8
echo SPEED_DONE
