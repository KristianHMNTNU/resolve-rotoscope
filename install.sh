#!/bin/zsh
# Installs Rotoscope. Safe to run again: updates the app and keeps settings and model weights.
#
#   - Python environment, app and model weights: ~/Library/Application Support/Rotoscope
#   - Resolve scripts: Workspace > Scripts (user Utility folder)
#   - Matte folder: ~/Movies/Rotoscope
#
# Restart DaVinci Resolve afterwards so the scripts show up.

set -e
SRC="${0:A:h}"
ROOT="$HOME/Library/Application Support/Rotoscope"
RESOLVE_SCRIPTS="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
UV="$HOME/.local/bin/uv"
CUTIE_COMMIT=ec5cdd4cf16f75c73ad785a2f96fb97dbad4125a
SAM_REPO="https://huggingface.co/square-zero-labs/sam2.1-tiny-video-onnx/resolve/main"

say() { print -P "%F{cyan}==>%f $*"; }

mkdir -p "$ROOT"/{models/cutie,models/onnx_sam21_tiny,vendor,jobs,logs,cache,app}

# ── Python ────────────────────────────────────────────────────────────────────
if [[ ! -x "$UV" ]]; then
  say "Installing uv (Python package manager) in ~/.local/bin"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
fi
if [[ ! -x "$ROOT/venv/bin/python" ]]; then
  say "Creating a Python 3.11 environment"
  "$UV" python install 3.11
  "$UV" venv --python 3.11 "$ROOT/venv"
fi
say "Installing Python packages (a few minutes the first time)"
"$UV" pip install --python "$ROOT/venv/bin/python" -q \
  "torch==2.2.2" "torchvision==0.17.2" "numpy<2" av opencv-python-headless onnxruntime \
  PySide6 "hydra-core>=1.3.2" omegaconf "einops>=0.6" pillow

# ── Cutie (tracking model code) ───────────────────────────────────────────────
if [[ ! -d "$ROOT/vendor/Cutie/cutie" ]]; then
  say "Downloading Cutie"
  git clone -q https://github.com/hkchengrex/Cutie "$ROOT/vendor/Cutie"
  git -C "$ROOT/vendor/Cutie" checkout -q $CUTIE_COMMIT
fi

# ── Model weights ─────────────────────────────────────────────────────────────
fetch() {  # url dest [md5]
  if [[ -f "$2" ]]; then
    if [[ -z "$3" || "$(md5 -q "$2")" == "$3" ]]; then return; fi
  fi
  say "Downloading $(basename "$2")"
  curl -L --fail -s -o "$2.part" "$1"
  if [[ -n "$3" && "$(md5 -q "$2.part")" != "$3" ]]; then
    print -P "%F{red}Checksum mismatch for $(basename "$2")%f"; rm -f "$2.part"; exit 1
  fi
  mv "$2.part" "$2"
}
fetch https://github.com/hkchengrex/Cutie/releases/download/v1.0/cutie-base-mega.pth \
  "$ROOT/models/cutie/cutie-base-mega.pth" a6071de6136982e396851903ab4c083a
fetch https://github.com/hkchengrex/Cutie/releases/download/v1.0/cutie-small-mega.pth \
  "$ROOT/models/cutie/cutie-small-mega.pth"
for f in vision_encoder.onnx mask_decoder.onnx; do
  fetch "$SAM_REPO/onnx/$f" "$ROOT/models/onnx_sam21_tiny/$f"
done
fetch "$SAM_REPO/constants.json" "$ROOT/models/onnx_sam21_tiny/constants.json"

# ── The app ───────────────────────────────────────────────────────────────────
say "Installing the app"
rsync -a --delete --exclude __pycache__ "$SRC/app/rotoscope/" "$ROOT/app/rotoscope/"

APPB="$ROOT/Rotoscope.app"
mkdir -p "$APPB/Contents/MacOS"
cat > "$APPB/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Rotoscope</string>
  <key>CFBundleDisplayName</key><string>Rotoscope</string>
  <key>CFBundleIdentifier</key><string>local.rotoscope.app</string>
  <key>CFBundleExecutable</key><string>rotoscope</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
cat > "$APPB/Contents/MacOS/rotoscope" <<'SH'
#!/bin/sh
ROOT="$HOME/Library/Application Support/Rotoscope"
export PYTHONPATH="$ROOT/app"
export PYTHONDONTWRITEBYTECODE=1
cd "$ROOT"
exec "$ROOT/venv/bin/python" -m rotoscope "$@" >>"$ROOT/logs/app_output.log" 2>&1
SH
chmod +x "$APPB/Contents/MacOS/rotoscope"

# ── Resolve script ────────────────────────────────────────────────────────────
say "Installing the Resolve scripts"
mkdir -p "$RESOLVE_SCRIPTS"
cp "$SRC"/resolve/*.lua "$RESOLVE_SCRIPTS/"

# ── Root folder ───────────────────────────────────────────────────────────────
ROOT_DIR=$("$ROOT/venv/bin/python" -c "import sys; sys.path.insert(0, '$ROOT/app'); from rotoscope import config; print(config.load_settings()['root_dir'])")
mkdir -p "$ROOT_DIR"

say "Checking the installation"
PYTHONPATH="$ROOT/app" "$ROOT/venv/bin/python" -c "
import torch, onnxruntime, av, cv2, PySide6, hydra
from rotoscope import config, gui, project, segmenter, tracker
print('  torch', torch.__version__, '| onnxruntime', onnxruntime.__version__, '| PySide6', PySide6.__version__)
"

print
say "Done. Restart DaVinci Resolve and find Rotoscope under Workspace > Scripts."
say "Mattes are saved in $ROOT_DIR"
