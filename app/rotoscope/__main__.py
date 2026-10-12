"""Entry point.

  python -m rotoscope --job job.json                    (started by the Resolve script)
  python -m rotoscope --file clip.mp4 [--start N --end M]  (standalone, for testing)
"""

import argparse
import glob
import json
import logging
import os
import sys
import time
import traceback

from . import config, storage

log = logging.getLogger("rotoscope")


def _clean_cache(max_age_days=60):
    cutoff = time.time() - max_age_days * 86400
    for f in glob.glob(os.path.join(config.CACHE_DIR, "*")):
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
        except OSError:
            pass


def _dark_palette(app):
    from PySide6.QtGui import QColor, QPalette
    app.setStyle("Fusion")
    pal = QPalette()
    for role, c in [(QPalette.ColorRole.Window, (45, 45, 45)), (QPalette.ColorRole.WindowText, (220, 220, 220)),
                    (QPalette.ColorRole.Base, (30, 30, 30)), (QPalette.ColorRole.AlternateBase, (45, 45, 45)),
                    (QPalette.ColorRole.Text, (220, 220, 220)), (QPalette.ColorRole.Button, (60, 60, 60)),
                    (QPalette.ColorRole.ButtonText, (220, 220, 220)), (QPalette.ColorRole.Highlight, (70, 130, 200)),
                    (QPalette.ColorRole.HighlightedText, (255, 255, 255)), (QPalette.ColorRole.ToolTipBase, (60, 60, 60)),
                    (QPalette.ColorRole.ToolTipText, (220, 220, 220))]:
        pal.setColor(role, QColor(*c))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(120, 120, 120))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(120, 120, 120))
    app.setPalette(pal)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rotoscope")
    ap.add_argument("--job")
    ap.add_argument("--file")
    ap.add_argument("--start", type=int)
    ap.add_argument("--end", type=int)
    ap.add_argument("--project", default="No project")
    a = ap.parse_args(argv)

    config.setup_logging()
    _clean_cache()
    os.environ.setdefault("QT_MAC_WANTS_LAYER", "1")

    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Rotoscope")
    _dark_palette(app)

    job = {}
    try:
        if a.job:
            with open(a.job) as f:
                job = json.load(f)
        else:
            path = a.file or QFileDialog.getOpenFileName(None, "Choose a video clip", os.path.expanduser("~/Movies"),
                                                         "Video (*.mp4 *.mov *.MP4 *.MOV *.mxf *.m4v)")[0]
            if not path:
                return 0
            job = {"file": path, "clip_name": os.path.basename(path), "project": a.project}
            if a.start is not None:
                job["start_frame"] = a.start
            if a.end is not None:
                job["end_frame"] = a.end
        log.info("job: %s", job)
        settings = config.load_settings()

        from .project import Project
        from .gui import MainWindow
        if not os.path.exists(job.get("file", "")):
            raise FileNotFoundError(f"Cannot find the source file:\n{job.get('file')}")
        project = Project(job, settings)
        win = MainWindow(project, settings)
        win.show()
        win.raise_()
        win.activateWindow()
        return app.exec()
    except Exception as e:  # noqa: BLE001
        log.error("startup failed:\n%s", traceback.format_exc())
        QMessageBox.critical(None, "Rotoscope", f"Could not start:\n\n{e}")
        if job:
            storage.write_result(job, {"status": "error", "message": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
