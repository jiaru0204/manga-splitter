# -*- coding: utf-8 -*-
"""
裁剪画布（QGraphicsView 版）。

替代旧版 tkinter Canvas 的手工坐标换算（_to_screen / _to_image / 可见区裁剪 /
草稿帧+精修两阶段渲染）—— Qt 的视图变换原生支持：
  - 滚轮缩放以鼠标为锚点（AnchorUnderMouse），无需手动算 pan；
  - 缩放/平移由显卡侧完成，拖框时只重画矢量覆盖层，不再每帧 PIL 缩放整图；
  - SmoothPixmapTransform 平滑缩放，画质与旧版 LANCZOS 相当。

场景图层（z 从低到高）：原图 0 < 框外压暗 1 < 基底预览 2 < 选框装饰 3。
鼠标交互（拖框 / 8 控制点 / 框外点击隐藏 / 取色）通过信号交给 CropPage 处理。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen, QBrush,
                           QPixmap, QFont)
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsItem,
                               QGraphicsPixmapItem, QGraphicsPathItem,
                               QGraphicsRectItem)

from . import theme

HANDLE_R = 6          # 控制点屏幕半径
MIN_SIZE = 8          # 选框最小尺寸（图像像素）


class CropRectItem(QGraphicsItem):
    """选框装饰：双线边框 + 三分线 + 8 控制点 + 尺寸胶囊。全部按场景坐标绘制，
    线宽/控制点/字号用 levelOfDetail 折算，保证任意缩放下视觉尺寸恒定。"""

    def __init__(self):
        super().__init__()
        self.rect = QRectF()
        self.setZValue(3)

    def set_rect(self, r: QRectF):
        self.prepareGeometryChange()
        self.rect = r
        self.update()

    def boundingRect(self) -> QRectF:
        return self.rect.adjusted(-40, -40, 40, 40)

    def paint(self, p: QPainter, option, widget=None):
        if self.rect.isNull() or self.rect.width() <= 0:
            return
        lod = option.levelOfDetailFromTransform(p.worldTransform())
        lod = max(lod, 1e-6)
        r = self.rect
        acc = QColor(theme.ACCENT)

        # 双线边框
        p.setPen(QPen(QColor(255, 255, 255, 220), 3 / lod))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)
        p.setPen(QPen(acc, 2 / lod))
        p.drawRect(r)

        # 三分线
        pen = QPen(acc, 1 / lod, Qt.DashLine)
        p.setPen(pen)
        for f in (1 / 3, 2 / 3):
            x = r.left() + r.width() * f
            y = r.top() + r.height() * f
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))

        # 8 控制点
        hr = HANDLE_R / lod
        p.setPen(QPen(acc, 2 / lod))
        p.setBrush(QBrush(QColor("#ffffff")))
        for hx, hy in handle_points(r).values():
            p.drawEllipse(QPointF(hx, hy), hr, hr)

        # 尺寸胶囊
        label = f"{int(r.width())} × {int(r.height())}"
        fs = 11 / lod
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(max(int(fs), 1))
        font.setBold(True)
        p.setFont(font)
        cx = (r.left() + r.right()) / 2
        cy = r.bottom() + 20 / lod
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(label) + 18 / lod
        th = fm.height() + 8 / lod
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(acc))
        p.drawRoundedRect(QRectF(cx - tw / 2, cy - th / 2, tw, th), th / 2, th / 2)
        p.setPen(QPen(QColor("#ffffff")))
        p.drawText(QRectF(cx - tw / 2, cy - th / 2, tw, th), Qt.AlignCenter, label)


def handle_points(r: QRectF) -> dict[str, tuple[float, float]]:
    mx, my = (r.left() + r.right()) / 2, (r.top() + r.bottom()) / 2
    return {
        "nw": (r.left(), r.top()), "n": (mx, r.top()), "ne": (r.right(), r.top()),
        "w": (r.left(), my), "e": (r.right(), my),
        "sw": (r.left(), r.bottom()), "s": (mx, r.bottom()),
        "se": (r.right(), r.bottom()),
    }


class CropView(QGraphicsView):
    """画布：显示原图 + 压暗 + 基底预览 + 选框；交互经信号上抛。"""

    pressed = Signal(QPointF)    # 左键按下（场景坐标）
    dragged = Signal(QPointF)    # 左键拖动
    released = Signal(QPointF)   # 左键抬起
    fit_requested = Signal()     # 右键 = 还原视图

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("stage")
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFrameShape(QGraphicsView.NoFrame)

        self.pix_item = QGraphicsPixmapItem()
        self.pix_item.setZValue(0)
        self._scene.addItem(self.pix_item)

        self.dim_item = QGraphicsPathItem()
        self.dim_item.setZValue(1)
        self.dim_item.setBrush(QBrush(QColor(20, 28, 40, 150)))
        self.dim_item.setPen(QPen(Qt.NoPen))
        self._scene.addItem(self.dim_item)

        self.base_left = QGraphicsRectItem()
        self.base_right = QGraphicsRectItem()
        for it in (self.base_left, self.base_right):
            it.setZValue(2)
            it.setPen(QPen(Qt.NoPen))
            self._scene.addItem(it)
        self.base_frame = QGraphicsRectItem()
        self.base_frame.setZValue(2.5)
        self.base_frame.setBrush(Qt.NoBrush)
        self._scene.addItem(self.base_frame)

        self.crop_item = CropRectItem()
        self._scene.addItem(self.crop_item)

        self._mouse_down = False

    # ------------------------------------------------------------ 内容

    def set_pixmap(self, pm: QPixmap):
        self.pix_item.setPixmap(pm)
        self._scene.setSceneRect(QRectF(0, 0, pm.width(), pm.height()))

    def image_rect(self) -> QRectF:
        return QRectF(self.pix_item.boundingRect())

    def has_image(self) -> bool:
        return not self.pix_item.pixmap().isNull()

    def set_dim(self, crop_rect: QRectF, on: bool = True):
        """框外压暗：图像区域挖掉选框。"""
        if not on or crop_rect.isNull():
            self.dim_item.setPath(QPainterPath())
            return
        path = QPainterPath()
        path.addRect(self.image_rect())
        path.addRect(crop_rect)
        path.setFillRule(Qt.OddEvenFill)
        self.dim_item.setPath(path)

    def set_base_preview(self, rect: QRectF | None, left: QColor, right: QColor,
                         edge: QColor):
        """基底预览：左/右两块填色 + 外框；rect=None 时隐藏。"""
        if rect is None or rect.isNull():
            for it in (self.base_left, self.base_right, self.base_frame):
                it.setRect(QRectF())
            return
        self.base_left.setRect(rect)
        self.base_left.setBrush(QBrush(left))
        if right != left:
            self.base_right.setRect(QRectF(rect.center().x(), rect.top(),
                                           rect.width() / 2, rect.height()))
            self.base_right.setBrush(QBrush(right))
        else:
            self.base_right.setRect(QRectF())
        self.base_frame.setRect(rect)
        pen = QPen(edge)
        pen.setWidthF(2.0)
        pen.setCosmetic(True)      # 线宽不随缩放变化
        self.base_frame.setPen(pen)

    def set_crop_rect(self, r: QRectF, show: bool):
        self.crop_item.set_rect(r)
        self.crop_item.setVisible(show)

    def view_scale(self) -> float:
        return self.transform().m11()

    # ------------------------------------------------------------ 视图操作

    def wheelEvent(self, e):
        if not self.has_image():
            return
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        cur = self.view_scale()
        # 限制缩放范围（相对适配尺寸的 0.05x ~ 40x 由 fit 比例决定，简单用绝对值）
        if cur * factor < 0.005 or cur * factor > 40:
            return
        self.scale(factor, factor)
        e.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.fit_requested.emit()
            e.accept()
            return
        if e.button() == Qt.LeftButton and self.has_image():
            self._mouse_down = True
            self.pressed.emit(self.mapToScene(e.position().toPoint()))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._mouse_down:
            self.dragged.emit(self.mapToScene(e.position().toPoint()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._mouse_down:
            self._mouse_down = False
            self.released.emit(self.mapToScene(e.position().toPoint()))
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def fit_view(self):
        if self.has_image():
            self.fitInView(self.image_rect().adjusted(-14, -14, 14, 14),
                           Qt.KeepAspectRatio)

    def hit_handle(self, scene_pos: QPointF, crop_rect: QRectF) -> str | None:
        """命中检测：返回控制点名或 None。容差按屏幕像素折算。"""
        tol = (HANDLE_R + 4) / max(self.view_scale(), 1e-6)
        for name, (hx, hy) in handle_points(crop_rect).items():
            if abs(scene_pos.x() - hx) <= tol and abs(scene_pos.y() - hy) <= tol:
                return name
        return None
