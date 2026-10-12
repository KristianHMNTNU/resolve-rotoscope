"""Paths and user settings."""

import json
import logging
import os

APP_DIR = os.path.expanduser("~/Library/Application Support/Rotoscope")
MODELS_DIR = os.path.join(APP_DIR, "models")
CUTIE_DIR = os.path.join(APP_DIR, "vendor", "Cutie")
JOBS_DIR = os.path.join(APP_DIR, "jobs")
CACHE_DIR = os.path.join(APP_DIR, "cache")
LOG_DIR = os.path.join(APP_DIR, "logs")
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")

DEFAULTS = {
    "root_dir": os.path.expanduser("~/Movies/Rotoscope"),
    # Cutie tracks on a downscaled copy; this is its shorter side in pixels (480 is Cutie's default).
    "internal_size": 480,
    "cutie_model": "base",
    "threads": 8,
    "overlay_opacity": 0.5,
}


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH) as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        filename=os.path.join(LOG_DIR, "app.log"), level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger().addHandler(logging.StreamHandler())
