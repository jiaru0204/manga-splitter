# -*- coding: utf-8 -*-
"""
交互渲染基准：模拟「拖框 60 帧」的重绘成本。
  旧路径（tkinter 版）：每帧 PIL 可见区裁剪 + LANCZOS 缩放 + 暗化 + 选框亮区裁剪
  新路径（Qt 版）：QPixmap.scaled 一次（视图变换由 Qt 完成，覆盖层为矢量）

用法：QT_QPA_PLATFORM=offscreen python tests/bench_render.py
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageEnhance
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap

FRAMES = 60
SRC_W, SRC_H = 2600, 1900
VIEW_W, VIEW_H = 900, 660


def old_path_frame(src: Image.Image):
    """复刻旧 crop_panel._redraw 的核心开销（可见区 = 整图适配时）。"""
    scale = min((VIEW_W - 28) / SRC_W, (VIEW_H - 28) / SRC_H)
    vw, vh = int(SRC_W * scale), int(SRC_H * scale)
    base = src.resize((vw, vh), Image.LANCZOS, reducing_gap=2.0)
    _dark = ImageEnhance.Brightness(base).enhance(0.42)
    _sel = base.crop((vw // 4, 0, vw // 2, vh))


def main():
    app = QApplication([])
    img = Image.new("RGB", (SRC_W, SRC_H), (120, 90, 200))
    # 造点细节，避免纯色图被过度优化
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    for i in range(200):
        d.rectangle((i * 13 % SRC_W, i * 29 % SRC_H,
                     i * 13 % SRC_W + 60, i * 29 % SRC_H + 80),
                    fill=(i % 255, 200 - i % 100, 60))
    pm = QPixmap(VIEW_W, VIEW_H)
    from PySide6.QtGui import QImage
    qimg = QImage(img.tobytes(), SRC_W, SRC_H, SRC_W * 3, QImage.Format_RGB888)
    pm = QPixmap.fromImage(qimg)

    t0 = time.perf_counter()
    for _ in range(FRAMES):
        old_path_frame(img)
    t_old = (time.perf_counter() - t0) / FRAMES * 1000

    t0 = time.perf_counter()
    for _ in range(FRAMES):
        _ = pm.scaled(VIEW_W, VIEW_H, aspectRatioMode=1,
                      mode=1)  # KeepAspectRatio + SmoothTransformation
    t_new = (time.perf_counter() - t0) / FRAMES * 1000

    print(f"拖框一帧重绘（{SRC_W}x{SRC_H} → {VIEW_W}x{VIEW_H}，{FRAMES} 帧平均）")
    print(f"  旧路径（PIL LANCZOS + 暗化 + 亮区）: {t_old:8.1f} ms/帧  ({1000/t_old:.1f} FPS)")
    print(f"  新路径（Qt 平滑缩放）:               {t_new:8.1f} ms/帧  ({1000/t_new:.1f} FPS)")
    print(f"  加速比: {t_old/t_new:.1f}x")


if __name__ == "__main__":
    main()
