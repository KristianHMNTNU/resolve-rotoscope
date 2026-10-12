"""Drive the main window offscreen: load, click, track, view modes, finish. Saves screenshots.

  QT_QPA_PLATFORM=offscreen python app/tests/test_gui.py <video> <start> <end> <outdir> x,y [x,y ...]
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from rotoscope import config  # noqa: E402
from rotoscope.gui import MainWindow  # noqa: E402
from rotoscope.project import Project  # noqa: E402


def wait(app, cond, timeout=600):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.02)
        if time.time() - t0 > timeout:
            raise TimeoutError
    app.processEvents()


def main():
    path, start, end, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    clicks = [tuple(float(v) for v in a.split(",")) for a in sys.argv[5:]]
    os.makedirs(out, exist_ok=True)
    root = tempfile.mkdtemp(prefix="rotoscope_gui_")
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.information = staticmethod(lambda *a, **k: print("INFO:", a[2:]))
    QMessageBox.critical = staticmethod(lambda *a, **k: print("ERROR:", a[2:]))
    app = QApplication(sys.argv[:1])
    from rotoscope.__main__ import _dark_palette
    _dark_palette(app)
    settings = dict(config.load_settings(), root_dir=root)
    job = {"file": path, "start_frame": start, "end_frame": end, "project": "GUI-test",
           "clip_name": os.path.basename(path), "result_path": os.path.join(root, "result.json")}
    win = MainWindow(Project(job, settings), settings)
    win.resize(1500, 900)
    win.show()
    wait(app, lambda: win.project.frames and not win._busy and win.tracker is not None)
    win.grab().save(os.path.join(out, "1_loaded.png"))

    win.goto(0)
    s = win.project.work_scale
    for x, y in clicks:
        win._on_click(x * s, y * s, True)
        wait(app, lambda: not win._busy)
    win.grab().save(os.path.join(out, "2_clicked.png"))

    t = time.time()
    win._track([-1, 1])
    wait(app, lambda: not win._busy)
    print(f"tracked in {time.time() - t:.1f}s; missing {len(win.project.missing_frames())}")
    win.goto(win.project.count - 1)
    win.grab().save(os.path.join(out, "3_tracked_overlay.png"))
    win._set_mode("cutout")
    win.grab().save(os.path.join(out, "4_cutout.png"))
    win.viewer.zoom = 4.0
    win.viewer.update()
    win.grab().save(os.path.join(out, "5_cutout_zoom.png"))

    win._finish()
    wait(app, lambda: os.path.exists(job["result_path"]))
    print("result:", json.load(open(job["result_path"])))
    print("lua:", open(os.path.splitext(job["result_path"])[0] + ".lua").read()[:300])


if __name__ == "__main__":
    main()
