# -*- coding: utf-8 -*-
"""离屏渲染界面截图，用于自检 UI 布局。用法：python tests/shot.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication
from manga_splitter.ui.main_window import MainWindow
from manga_splitter.ui.theme import QSS

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shots")


def sample_folder():
    d = os.path.join(tempfile.gettempdir(), "manga_shot", "Lm-dcb Vol_04")
    os.makedirs(d, exist_ok=True)
    for i in range(12):
        im = Image.new("RGB", (2600, 1900), (245, 243, 238))
        dr = ImageDraw.Draw(im)
        dr.rectangle((60, 60, 1270, 1840), outline=(60, 60, 60), width=6)
        dr.rectangle((1330, 60, 2540, 1840), outline=(60, 60, 60), width=6)
        for k in range(6):
            dr.rectangle((120, 200 + k * 250, 1200, 330 + k * 250), fill=(90, 90, 90))
            dr.rectangle((1390, 200 + k * 250, 2470, 330 + k * 250), fill=(90, 90, 90))
        im.save(os.path.join(d, f"{i+1:03d}.jpg"), quality=80)
    return d


def main():
    app = QApplication([])
    app.setStyleSheet(QSS)
    os.makedirs(OUT, exist_ok=True)
    src = sample_folder()

    win = MainWindow()
    if win._update_worker is not None:
        win._update_worker.stop()
    win.resize(1440, 900)
    win.split_page.clear_folders()
    win.split_page._append_folder(src)
    win.show()
    app.processEvents()
    win.grab().save(os.path.join(OUT, "1-自动分页.png"))

    win.nav.setCurrentRow(1)
    app.processEvents()
    cp = win.crop_page
    cp.set_folders([src])
    app.processEvents()
    cp._load(0)
    cp.base_on_chk.setChecked(True)
    cp.base_w_edit.setText("1400")
    cp.base_h_edit.setText("1900")
    cp.w_edit.setText("1300")
    cp.h_edit.setText("1900")
    cp.apply_size()
    app.processEvents()
    for _ in range(3):
        app.processEvents()
    win.grab().save(os.path.join(OUT, "2-框选裁剪.png"))
    print("✔ 截图已写入", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
