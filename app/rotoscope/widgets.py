"""Custom widgets: the picture viewer (clicks, box, zoom) and the timeline bar."""

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


def to_qimage(rgb):
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    img._keep = rgb  # keep the buffer alive as long as the image
    return img


class Viewer(QWidget):
    """Shows one frame. Left click = object, right click (or Ctrl+click) = not object,
    Shift+drag = box. Mouse wheel zooms, Alt+drag or middle-drag pans, double-click fits."""

    clicked = Signal(float, float, bool)          # x, y (image pixels), positive
    boxDrawn = Signal(float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(480, 270)
        self.setMouseTracking(True)
        self.image = None
        self.points = []     # (x, y, positive)
        self.box = None      # (x0, y0, x1, y1)
        self.zoom = 1.0
        self.center = None   # image coords at the centre of the view
        self._drag_box = None
        self._pan_from = None
        self.message = ""

    def set_image(self, qimage):
        first = self.image is None
        self.image = qimage
        if first:
            self.fit()
        self.update()

    def set_marks(self, points, box):
        self.points = points
        self.box = box
        self.update()

    def fit(self):
        self.zoom = 1.0
        if self.image is not None:
            self.center = QPointF(self.image.width() / 2, self.image.height() / 2)
        self.update()

    # geometry
    def _scale(self):
        iw, ih = self.image.width(), self.image.height()
        return min(self.width() / iw, self.height() / ih) * self.zoom

    def _img_to_view(self, x, y):
        s = self._scale()
        return QPointF((x - self.center.x()) * s + self.width() / 2, (y - self.center.y()) * s + self.height() / 2)

    def _view_to_img(self, p):
        s = self._scale()
        return ((p.x() - self.width() / 2) / s + self.center.x(), (p.y() - self.height() / 2) / s + self.center.y())

    def _inside(self, x, y):
        return 0 <= x < self.image.width() and 0 <= y < self.image.height()

    # painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(30, 30, 30))
        if self.image is None:
            p.setPen(QColor(200, 200, 200))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message or "Loading…")
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 2)
        tl = self._img_to_view(0, 0)
        br = self._img_to_view(self.image.width(), self.image.height())
        p.drawImage(QRectF(tl, br), self.image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.box:
            x0, y0, x1, y1 = self.box
            p.setPen(QPen(QColor(80, 160, 255), 2))
            p.drawRect(QRectF(self._img_to_view(x0, y0), self._img_to_view(x1, y1)))
        if self._drag_box:
            a, b = self._drag_box
            p.setPen(QPen(QColor(80, 160, 255), 1, Qt.PenStyle.DashLine))
            p.drawRect(QRectF(a, b).normalized())
        for x, y, pos in self.points:
            c = self._img_to_view(x, y)
            p.setPen(QPen(QColor(0, 0, 0), 3))
            p.setBrush(QColor(60, 220, 90) if pos else QColor(240, 60, 60))
            p.drawEllipse(c, 6, 6)
        if self.message:
            p.setPen(QColor(255, 255, 255))
            p.fillRect(QRectF(8, 8, p.fontMetrics().horizontalAdvance(self.message) + 16, 26), QColor(0, 0, 0, 160))
            p.drawText(QRectF(16, 8, 2000, 26), Qt.AlignmentFlag.AlignVCenter, self.message)

    # mouse
    def mousePressEvent(self, e):
        if self.image is None:
            return
        mods = e.modifiers()
        if e.button() == Qt.MouseButton.MiddleButton or (
                e.button() == Qt.MouseButton.LeftButton and mods & Qt.KeyboardModifier.AltModifier):
            self._pan_from = (e.position(), QPointF(self.center))
        elif e.button() == Qt.MouseButton.LeftButton and mods & Qt.KeyboardModifier.ShiftModifier:
            self._drag_box = (e.position(), e.position())
        elif e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            x, y = self._view_to_img(e.position())
            if self._inside(x, y):
                positive = e.button() == Qt.MouseButton.LeftButton and not (
                    mods & Qt.KeyboardModifier.ControlModifier or mods & Qt.KeyboardModifier.MetaModifier)
                self.clicked.emit(x, y, positive)

    def mouseMoveEvent(self, e):
        if self._pan_from:
            start, c0 = self._pan_from
            s = self._scale()
            d = e.position() - start
            self.center = QPointF(c0.x() - d.x() / s, c0.y() - d.y() / s)
            self.update()
        elif self._drag_box:
            self._drag_box = (self._drag_box[0], e.position())
            self.update()

    def mouseReleaseEvent(self, e):
        if self._pan_from:
            self._pan_from = None
        elif self._drag_box:
            a, b = self._drag_box
            self._drag_box = None
            x0, y0 = self._view_to_img(a)
            x1, y1 = self._view_to_img(b)
            w, h = self.image.width(), self.image.height()
            x0, x1 = sorted((min(max(x0, 0), w - 1), min(max(x1, 0), w - 1)))
            y0, y1 = sorted((min(max(y0, 0), h - 1), min(max(y1, 0), h - 1)))
            if x1 - x0 > 4 and y1 - y0 > 4:
                self.boxDrawn.emit(x0, y0, x1, y1)
            self.update()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton:
            self.fit()

    def wheelEvent(self, e):
        if self.image is None:
            return
        before = self._view_to_img(e.position())
        f = 1.0015 ** e.angleDelta().y()
        self.zoom = min(16.0, max(1.0, self.zoom * f))
        after = self._view_to_img(e.position())
        self.center = QPointF(self.center.x() + before[0] - after[0], self.center.y() + before[1] - after[1])
        if self.zoom == 1.0:
            self.center = QPointF(self.image.width() / 2, self.image.height() / 2)
        self.update()


class TimelineBar(QWidget):
    """Frames as a bar: green where a mask exists, yellow ticks where you clicked, red playhead."""

    frameChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.count = 1
        self.current = 0
        self.has_mask = np.zeros(1, bool)
        self.keyframes = []

    def set_state(self, count, current, has_mask, keyframes):
        self.count, self.current = max(1, count), current
        self.has_mask, self.keyframes = has_mask, keyframes
        self.update()

    def _x(self, i):
        return 6 + (self.width() - 12) * (i + 0.5) / self.count

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect().adjusted(6, 8, -6, -8)
        p.fillRect(r, QColor(60, 60, 60))
        w = r.width() / self.count
        # draw runs of frames with masks
        i = 0
        while i < self.count:
            if i < len(self.has_mask) and self.has_mask[i]:
                j = i
                while j + 1 < self.count and self.has_mask[j + 1]:
                    j += 1
                p.fillRect(QRectF(r.left() + i * w, r.top(), (j - i + 1) * w, r.height()), QColor(70, 170, 90))
                i = j + 1
            else:
                i += 1
        p.setPen(QPen(QColor(250, 210, 60), 2))
        for k in self.keyframes:
            x = self._x(k)
            p.drawLine(QPointF(x, 2), QPointF(x, self.height() - 2))
        p.setPen(QPen(QColor(240, 50, 50), 2))
        x = self._x(self.current)
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))

    def _seek(self, e):
        i = int((e.position().x() - 6) / max(1, self.width() - 12) * self.count)
        i = min(max(i, 0), self.count - 1)
        if i != self.current:
            self.frameChanged.emit(i)

    def mousePressEvent(self, e):
        self._seek(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._seek(e)
