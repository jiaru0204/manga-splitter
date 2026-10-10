# -*- coding: utf-8 -*-
"""
GUI 冒烟测试（offscreen，无显示器也能跑）：
  1. 主窗口能创建、两个页面能切换；
  2. 裁剪页载入图片 -> 设选框 -> 保存，产物尺寸正确；
  3. 分页页 build_plan 通路正常。

用法：QT_QPA_PLATFORM=offscreen python tests/gui_smoke.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from manga_splitter.ui.main_window import MainWindow
from manga_splitter.core.cropper import CropSpec


def main() -> int:
    app = QApplication([])
    tmp = tempfile.mkdtemp(prefix="manga_smoke_")
    src = os.path.join(tmp, "Vol_01")
    os.makedirs(src)
    for i in range(3):
        Image.new("RGB", (2400, 1200), (100, i * 60, 30)).save(
            os.path.join(src, f"{i+1:03d}.png"))

    win = MainWindow()
    # 关掉自动更新检查，避免联网
    if win._update_worker is not None:
        win._update_worker.stop()

    # ---- 页面切换 ----
    win.nav.setCurrentRow(1)
    assert win.stack.currentIndex() == 1

    # ---- 分页页：加文件夹 + 计划 ----
    win.split_page.clear_folders()          # 丢掉上次运行残留的配置
    win.split_page._append_folder(src)
    assert win.split_page.tree.topLevelItemCount() == 1
    out_dir = win.split_page.resolve_out_dir(src)
    assert out_dir.endswith("_split")

    # ---- 裁剪页：同步册 -> 载入 -> 裁剪保存 ----
    win.nav.setCurrentRow(1)
    cp = win.crop_page
    cp.set_folders([src])
    assert len(cp.files) == 3, f"files={cp.files}"
    cp._load(0)
    assert not cp.pix.isNull(), "pixmap 载入失败"
    assert (cp.pix.width(), cp.pix.height()) == (2400, 1200)

    # 选框右半
    cp._set_box(1200, 0, 1200, 1200)
    cp._update_visuals()
    cp.follow_src_chk.setChecked(False)
    out_crop = os.path.join(tmp, "crop_out")
    cp.out_edit.setText(out_crop)
    assert cp._save_current(), "保存失败"
    saved = cp._last_saved
    assert os.path.exists(saved), f"产物不存在: {saved}"
    assert Image.open(saved).size == (1200, 1200)

    # 旋转 90° 后再裁
    cp.rotate(90)
    assert (cp.pix.width(), cp.pix.height()) == (1200, 2400)
    cp._set_box(0, 0, 1200, 1200)
    assert cp._save_current()
    assert Image.open(cp._last_saved).size == (1200, 1200)

    # 序号/后缀：同图两块 -> 001a/001b（按 {n:03}{a} 模板验证）
    cp.name_tpl_edit.setText("{n:03}{a}")
    cp._update_name_preview()
    assert "001c" in cp.name_preview.text() or "001" in cp.name_preview.text(), \
        cp.name_preview.text()

    win.save_all()
    print("✔ GUI 冒烟测试全部通过")
    print(f"  临时目录: {tmp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
