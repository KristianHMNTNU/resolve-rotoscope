"""Main window."""

import logging
import threading
import time
import traceback

import cv2
import numpy as np
from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QRadioButton, QSlider, QVBoxLayout, QWidget)

from . import config, storage
from .widgets import TimelineBar, Viewer, to_qimage

log = logging.getLogger(__name__)


class Worker(QThread):
    """Runs fn(progress, cancelled) in the background."""

    progress = Signal(int, int, float)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.cancel_requested = False

    def run(self):
        try:
            res = self.fn(lambda d, t, spf=0.0: self.progress.emit(d, t, spf), lambda: self.cancel_requested)
            self.done.emit(res)
        except Exception as e:  # noqa: BLE001 - shown to the user
            log.error("worker failed:\n%s", traceback.format_exc())
            self.failed.emit(str(e))


def _checker(h, w, sq=16):
    yy, xx = np.mgrid[0:h, 0:w]
    c = (((yy // sq) + (xx // sq)) % 2).astype(bool)
    out = np.empty((h, w, 3), np.uint8)
    out[c], out[~c] = 190, 120
    return out


def _fmt_time(sec):
    sec = max(0, int(sec))
    return f"{sec // 60} min {sec % 60:02d} s" if sec >= 60 else f"{sec} s"


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.settings = dict(settings)
        lay = QFormLayout(self)
        row = QHBoxLayout()
        self.root = QLineEdit(self.settings["root_dir"])
        b = QPushButton("Choose…")
        b.clicked.connect(self._pick)
        row.addWidget(self.root)
        row.addWidget(b)
        lay.addRow("Matte folder:", row)
        self.quality = QComboBox()
        self.sizes = [(360, "Fast (360)"), (480, "Normal (480)"), (600, "High (600)"), (720, "Very high (720)")]
        for v, t in self.sizes:
            self.quality.addItem(t, v)
        self.quality.setCurrentIndex(max(0, [v for v, _ in self.sizes].index(self.settings["internal_size"])
                                         if self.settings["internal_size"] in [v for v, _ in self.sizes] else 1))
        lay.addRow("Tracking resolution:", self.quality)
        self.model = QComboBox()
        self.model.addItem("Cutie base (best)", "base")
        self.model.addItem("Cutie small (faster)", "small")
        self.model.setCurrentIndex(0 if self.settings["cutie_model"] == "base" else 1)
        lay.addRow("Tracking model:", self.model)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Matte folder", self.root.text())
        if d:
            self.root.setText(d)

    def result_settings(self):
        self.settings.update(root_dir=self.root.text(), internal_size=self.quality.currentData(),
                             cutie_model=self.model.currentData())
        return self.settings


class MainWindow(QMainWindow):
    def __init__(self, project, settings):
        super().__init__()
        self.project = project
        self.settings = settings
        self.segmenter = None
        self.tracker = None
        self.worker = None
        self.current = 0
        self.mode = "overlay"
        self.finished_ok = False
        self._checker = None
        self._history = {}   # frame -> list of ("pos"|"neg"|"box", value); for undo
        self._prev_prob = {}  # frame -> mask before the first click on that frame

        name = project.job.get("clip_name") or project.info.path
        self.setWindowTitle(f"Rotoscope – {name}  (frames {project.start}–{project.end})")
        self.resize(1500, 900)
        self._build_ui()
        self._shortcuts()
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._play_step)
        self._set_busy(True, "Loading clip…")
        QTimer.singleShot(50, self._start_loading)

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        left = QVBoxLayout()
        self.viewer = Viewer()
        self.viewer.clicked.connect(self._on_click)
        self.viewer.boxDrawn.connect(self._on_box)
        left.addWidget(self.viewer, 1)
        self.timeline = TimelineBar()
        self.timeline.frameChanged.connect(self.goto)
        left.addWidget(self.timeline)
        nav = QHBoxLayout()
        for text, fn, tip in [("⏮", lambda: self.goto(0), "First frame (Home)"),
                              ("◀", lambda: self.goto(self.current - 1), "Previous frame (←)"),
                              ("▶︎ Play", self._toggle_play, "Play / pause (Space)"),
                              ("▶", lambda: self.goto(self.current + 1), "Next frame (→)"),
                              ("⏭", lambda: self.goto(self.project.count - 1), "Last frame (End)")]:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            nav.addWidget(b)
            if text.startswith("▶︎"):
                self.play_btn = b
        self.frame_label = QLabel("")
        nav.addWidget(self.frame_label, 1)
        fit = QPushButton("Fit view")
        fit.clicked.connect(self.viewer.fit)
        nav.addWidget(fit)
        left.addLayout(nav)
        root.addLayout(left, 1)

        panel = QVBoxLayout()
        panel_w = QWidget()
        panel_w.setLayout(panel)
        panel_w.setFixedWidth(330)
        root.addWidget(panel_w)

        g = QGroupBox("1. Select the object")
        v = QVBoxLayout(g)
        help_ = QLabel("Click: part of the object\nRight-click: not part of the object\n"
                       "Shift + drag: draw a box around it\nScroll: zoom · Alt + drag: pan\n\n"
                       "One click often selects only a part (a hat, a jacket). "
                       "Click head, body and legs, or draw a box.")
        help_.setWordWrap(True)
        help_.setStyleSheet("color: #aaa")
        v.addWidget(help_)
        row = QHBoxLayout()
        self.undo_btn = QPushButton("Undo click")
        self.undo_btn.clicked.connect(self._undo_click)
        self.clear_btn = QPushButton("Clear this frame")
        self.clear_btn.clicked.connect(self._clear_clicks)
        row.addWidget(self.undo_btn)
        row.addWidget(self.clear_btn)
        v.addLayout(row)
        panel.addWidget(g)

        g = QGroupBox("2. Track")
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        self.back_btn = QPushButton("◀ Backward")
        self.back_btn.clicked.connect(lambda: self._track([-1]))
        self.both_btn = QPushButton("◀ Both ▶")
        self.both_btn.clicked.connect(lambda: self._track([-1, 1]))
        self.fwd_btn = QPushButton("Forward ▶")
        self.fwd_btn.clicked.connect(lambda: self._track([1]))
        for b in (self.back_btn, self.both_btn, self.fwd_btn):
            row.addWidget(b)
        v.addLayout(row)
        note = QLabel("Tracking stops at the next frame you clicked on. To fix a mistake, "
                      "click on the frame where it goes wrong and track on from there.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #aaa")
        v.addWidget(note)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        v.addWidget(self.progress)
        self.progress_label = QLabel("")
        v.addWidget(self.progress_label)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setEnabled(False)
        v.addWidget(self.cancel_btn)
        panel.addWidget(g)

        g = QGroupBox("3. Review")
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        for key, text in [("overlay", "Matte"), ("cutout", "Cut-out"), ("original", "Original")]:
            rb = QRadioButton(text)
            rb.setChecked(key == "overlay")
            rb.toggled.connect(lambda on, k=key: on and self._set_mode(k))
            self.mode_group.addButton(rb)
            row.addWidget(rb)
        v.addLayout(row)
        self.opacity = self._slider(v, "Overlay opacity", 0, 100,
                                    int(self.settings.get("overlay_opacity", 0.5) * 100), lambda _: self.refresh())
        panel.addWidget(g)

        g = QGroupBox("4. Edges")
        v = QVBoxLayout(g)
        e = self.project.edge
        self.feather = self._slider(v, "Feather", 0, 200, int(e["feather"] * 10), self._edges_changed, 0.1, "px")
        self.grow = self._slider(v, "Shrink / grow", -200, 200, int(e["grow"] * 10), self._edges_changed, 0.1, "px")
        self.smooth = self._slider(v, "Temporal smoothing", 0, 100, int(e["smooth"] * 100), self._edges_changed, 1, "%")
        panel.addWidget(g)

        panel.addStretch(1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        panel.addWidget(self.status)
        row = QHBoxLayout()
        s = QPushButton("Settings")
        s.clicked.connect(self._open_settings)
        row.addWidget(s)
        self.done_btn = QPushButton("Done")
        self.done_btn.setStyleSheet("QPushButton { font-weight: bold; padding: 8px; }")
        self.done_btn.clicked.connect(self._finish)
        row.addWidget(self.done_btn, 1)
        panel.addLayout(row)

    def _slider(self, layout, title, lo, hi, val, cb, unit_scale=None, unit=""):
        lab = QLabel()
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(val)

        def update_label(x):
            if unit_scale is None:
                lab.setText(f"{title}: {x} %")
            elif unit == "%":
                lab.setText(f"{title}: {x} %")
            else:
                lab.setText(f"{title}: {x * unit_scale:+.1f} {unit}" if lo < 0 else f"{title}: {x * unit_scale:.1f} {unit}")

        update_label(val)
        sl.valueChanged.connect(update_label)
        sl.valueChanged.connect(cb)
        layout.addWidget(lab)
        layout.addWidget(sl)
        return sl

    def _shortcuts(self):
        for key, fn in [(Qt.Key.Key_Left, lambda: self.goto(self.current - 1)),
                        (Qt.Key.Key_Right, lambda: self.goto(self.current + 1)),
                        (QKeySequence("Shift+Left"), lambda: self.goto(self.current - 10)),
                        (QKeySequence("Shift+Right"), lambda: self.goto(self.current + 10)),
                        (Qt.Key.Key_Home, lambda: self.goto(0)),
                        (Qt.Key.Key_End, lambda: self.goto(self.project.count - 1)),
                        (Qt.Key.Key_Space, self._toggle_play),
                        (QKeySequence.StandardKey.Undo, self._undo_click)]:
            QShortcut(QKeySequence(key), self, activated=fn)

    # ── loading ─────────────────────────────────────────────────────────────

    def _start_loading(self):
        p = self.project

        def load_models(errors):
            try:
                from .segmenter import Segmenter
                from .tracker import Tracker
                self.segmenter = Segmenter(threads=4)
                self.tracker = Tracker(self.settings["cutie_model"], self.settings["threads"])
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def job(progress, cancelled):
            t0 = time.perf_counter()
            errors = []
            models = threading.Thread(target=load_models, args=(errors,))
            models.start()   # models load while the frames are decoded
            p.load(progress=lambda d, t: progress(d, p.count), cancel=cancelled)
            log.info("loaded %d frames in %.1fs", p.count, time.perf_counter() - t0)
            restored = p.restore()
            models.join()
            if errors:
                raise errors[0]
            log.info("ready after %.1fs", time.perf_counter() - t0)
            return restored

        self._run(job, self._loaded, label="Reading frames from the source file")

    def _loaded(self, restored):
        p = self.project
        self._history = {k: [("pos", v) for v in kf["pos"]] + [("neg", v) for v in kf["neg"]]
                         + ([("box", kf["box"])] if kf.get("box") else []) for k, kf in p.keyframes.items()}
        self.feather.setValue(int(p.edge["feather"] * 10))
        self.grow.setValue(int(p.edge["grow"] * 10))
        self.smooth.setValue(int(p.edge["smooth"] * 100))
        start = min(p.keyframes) if p.keyframes else 0
        self.goto(start)
        if restored:
            self._say("Picked up where you left off. Click to fix things, or press Done.")
        else:
            self._say("Click on the object you want to cut out.")

    # ── frame display ────────────────────────────────────────────────────────

    def goto(self, i):
        if not self.project.frames:
            return
        self.current = int(min(max(i, 0), self.project.count - 1))
        self.refresh()

    def _set_mode(self, mode):
        self.mode = mode
        self.refresh()

    def refresh(self):
        p = self.project
        if not p.frames:
            return
        i = self.current
        base = p.frames[i]
        h, w = base.shape[:2]
        if self.mode == "original" or p.probs[i] is None:
            img = base
        else:
            m = p.matte_at(i, (w, h), p.work_scale).astype(np.float32)[..., None] / 255
            if self.mode == "overlay":
                a = self.opacity.value() / 100.0
                tint = np.array([255, 40, 40], np.float32)
                img = (base * (1 - a * m) + tint * (a * m)).astype(np.uint8)
                edge = cv2.morphologyEx((m[..., 0] > 0.5).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
                img[edge > 0] = (255, 230, 0)
            else:
                if self._checker is None or self._checker.shape[:2] != (h, w):
                    self._checker = _checker(h, w)
                img = (base * m + self._checker * (1 - m)).astype(np.uint8)
        self.viewer.set_image(to_qimage(img))
        kf = p.keyframes.get(i)
        pts = ([(x, y, True) for x, y in kf["pos"]] + [(x, y, False) for x, y in kf["neg"]]) if kf else []
        self.viewer.set_marks(pts, kf.get("box") if kf else None)
        has = np.array([q is not None for q in p.probs])
        self.timeline.set_state(p.count, i, has, sorted(p.keyframes))
        sec = (p.start + i) / float(p.fps)
        self.frame_label.setText(f"  Frame {i + 1} / {p.count}   (source {p.start + i}, "
                                 f"{int(sec // 60):02d}:{sec % 60:05.2f})"
                                 + ("   ● clicked" if kf else "")
                                 + ("   – no matte" if p.probs[i] is None else ""))

    def _toggle_play(self):
        if self.play_timer.isActive():
            self.play_timer.stop()
            self.play_btn.setText("▶︎ Play")
        else:
            if self.current >= self.project.count - 1:
                self.goto(0)
            self.play_timer.start(int(1000 / float(self.project.fps)))
            self.play_btn.setText("⏸ Pause")

    def _play_step(self):
        if self.current >= self.project.count - 1:
            self._toggle_play()
        else:
            self.goto(self.current + 1)

    def _edges_changed(self, _=None):
        self.project.edge.update(feather=self.feather.value() / 10, grow=self.grow.value() / 10,
                                 smooth=self.smooth.value() / 100)
        self.project.dirty = True
        self.refresh()

    # ── clicks ─────────────────────────────────────────────────────────────

    def _on_click(self, x, y, positive):
        self._add_prompt(("pos" if positive else "neg", [x, y]))

    def _on_box(self, x0, y0, x1, y1):
        self._add_prompt(("box", [x0, y0, x1, y1]))

    def _add_prompt(self, item):
        if self._busy:
            return
        i = self.current
        p = self.project
        if i not in p.keyframes:
            self._prev_prob[i] = p.probs[i]
            p.keyframes[i] = {"pos": [], "neg": [], "box": None}
            self._history[i] = []
        self._history[i].append(item)
        self._rebuild_keyframe(i)
        self._segment(i)

    def _rebuild_keyframe(self, i):
        kf = {"pos": [], "neg": [], "box": None}
        for kind, val in self._history.get(i, []):
            if kind == "box":
                kf["box"] = val
            else:
                kf[kind].append(val)
        self.project.keyframes[i] = kf

    def _segment(self, i):
        p = self.project
        kf = p.keyframes.get(i)
        if not kf or (not kf["pos"] and not kf["box"]):
            # only negative clicks so far: nothing to segment yet
            self.refresh()
            if kf and kf["neg"]:
                self._say("Add at least one click on the object (left click).")
            return
        first = ("frame", i) != getattr(self.segmenter, "_cache_key", None)

        def job(progress, cancelled):
            return p.segment_keyframe(self.segmenter, i)

        self._run(job, lambda _: self._after_segment(i), label="Computing matte…" if first else None, quiet=not first)

    def _after_segment(self, i):
        self.refresh()
        self._say("Does the matte look right? Click more to fix it, then track.")

    def _undo_click(self):
        i = self.current
        if self._busy or not self._history.get(i):
            return
        self._history[i].pop()
        if not self._history[i]:
            self._clear_clicks()
            return
        self._rebuild_keyframe(i)
        self._segment(i)

    def _clear_clicks(self):
        i = self.current
        if self._busy or i not in self.project.keyframes:
            return
        self.project.keyframes.pop(i, None)
        self._history.pop(i, None)
        self.project.probs[i] = self._prev_prob.pop(i, None)
        self.refresh()

    # ── tracking ─────────────────────────────────────────────────────────────

    def _track(self, directions):
        p = self.project
        if self._busy:
            return
        if p.probs[self.current] is None:
            QMessageBox.information(self, "Rotoscope", "There is no matte on this frame yet.\n"
                                                       "Click on the object first.")
            return
        seed = self.current
        names = {-1: "backward", 1: "forward"}

        def job(progress, cancelled):
            total = 0
            for d in directions:
                if cancelled():
                    break
                self._direction_label = names[d]
                total += p.track(self.tracker, seed, d, progress=progress, cancel=cancelled)
            return total

        self._direction_label = names[directions[0]]
        self._run(job, self._after_track, label="Tracking", cancellable=True)

    def _after_track(self, n):
        self.refresh()
        missing = len(self.project.missing_frames())
        if missing:
            self._say(f"Tracked {n} frames. {missing} frames have no matte yet.")
        else:
            self._say(f"Tracked {n} frames. Step through and look for mistakes. Press Done when you are happy.")

    # ── background jobs ───────────────────────────────────────────────────────

    _busy = False

    def _run(self, fn, on_done, label=None, cancellable=False, quiet=False):
        self.worker = Worker(fn, self)
        self._job_label = label
        self._t0 = time.perf_counter()
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(lambda r: (self._set_busy(False), on_done(r)))
        self.worker.failed.connect(self._on_failed)
        self._set_busy(True, label if not quiet else None, cancellable)
        self.worker.start()

    def _on_progress(self, done, total, spf):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        txt = self._job_label or ""
        if self._job_label == "Tracking":
            txt = f"Tracking {getattr(self, '_direction_label', '')}: {done} / {total}"
            if spf > 0:
                txt += f"  ·  {spf:.2f} s/frame  ·  about {_fmt_time(spf * (total - done))} left"
            if done % 5 == 0:
                self.refresh()
        elif total:
            txt += f": {done} / {total}"
        self.progress_label.setText(txt)

    def _on_failed(self, msg):
        self._set_busy(False)
        QMessageBox.critical(self, "Rotoscope", f"Something went wrong:\n\n{msg}\n\nDetails are in the log:\n"
                                                f"{config.LOG_DIR}/app.log")
        if not self.project.frames:
            self.close()

    def _set_busy(self, busy, label=None, cancellable=False):
        self._busy = busy
        for b in (self.back_btn, self.both_btn, self.fwd_btn, self.done_btn, self.undo_btn, self.clear_btn):
            b.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy and cancellable)
        if busy and label:
            self.progress.setRange(0, 0)
            self.progress_label.setText(label)
            self.viewer.message = label if not self.project.frames else ""
        if not busy:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress_label.setText("")
            self.viewer.message = ""
        self.viewer.update()

    def _cancel(self):
        if self.worker:
            self.worker.cancel_requested = True
            self.progress_label.setText("Cancelling…")

    def _say(self, text):
        self.status.setText(text)

    # ── settings / finish / close ─────────────────────────────────────────────

    def _open_settings(self):
        d = SettingsDialog(self.settings, self)
        if d.exec():
            new = d.result_settings()
            reload_model = new["cutie_model"] != self.settings["cutie_model"]
            self.settings.update(new)
            config.save_settings(self.settings)
            if reload_model and not self._busy:
                def job(progress, cancelled):
                    from .tracker import Tracker
                    self.tracker = Tracker(self.settings["cutie_model"], self.settings["threads"])
                self._run(job, lambda _: self._say("Tracking model loaded."), label="Loading tracking model…")

    def _finish(self):
        p = self.project
        if self._busy:
            return
        missing = p.missing_frames()
        if len(missing) == p.count:
            QMessageBox.information(self, "Rotoscope", "There is no matte yet. Click on the object and track first.")
            return
        if missing:
            r = QMessageBox.question(self, "Rotoscope",
                                     f"{len(missing)} of {p.count} frames have no matte and will be fully transparent.\n\n"
                                     "Save anyway?")
            if r != QMessageBox.StandardButton.Yes:
                return

        def job(progress, cancelled):
            return p.save(progress=lambda d: progress(d, p.count), cancel=cancelled)

        self._run(job, self._saved, label="Saving the matte at full resolution", cancellable=True)

    def _saved(self, path):
        if not path:
            self._say("Saving cancelled.")
            return
        p = self.project
        first, pattern = storage.sequence_paths(path)
        storage.write_result(p.job, {
            "status": "done", "matte": first, "matte_dir": path, "matte_pattern": pattern,
            "start_frame": p.start, "end_frame": p.end, "frames": p.count,
            "fps": str(p.fps), "width": p.info.width, "height": p.info.height, "source": p.info.path,
            "session": f"{p.folder()}/session.json"})
        self.finished_ok = True
        log.info("saved %s", path)
        self.close()

    def closeEvent(self, e):
        if self._busy and not self.finished_ok:
            if self.worker:
                self.worker.cancel_requested = True
                self.worker.wait(5000)
        if not self.finished_ok:
            if self.project.dirty:
                r = QMessageBox.question(self, "Rotoscope", "Close without saving?\n\nNothing will change in Resolve.")
                if r != QMessageBox.StandardButton.Yes:
                    e.ignore()
                    return
            storage.write_result(self.project.job, {"status": "cancelled"})
        saved = config.load_settings()   # only store what the user changes here, nothing transient
        saved["overlay_opacity"] = self.opacity.value() / 100
        config.save_settings(saved)
        e.accept()
