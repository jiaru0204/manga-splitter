# -*- coding: utf-8 -*-
"""应用入口：python -m manga_splitter 或打包后的 exe 都从这里启动。"""

from __future__ import annotations

import os
import sys


def _selftest() -> int:
    """命令行自检：不弹窗，跑一遍「分页 + 裁剪」核心链路，用于验证打包产物。"""
    import tempfile
    from PIL import Image
    from . import __version__
    from .core import splitter, cropper
    from .core.imaging import list_images

    tmp = tempfile.mkdtemp(prefix="manga_selftest_")
    src = os.path.join(tmp, "Vol_01")
    os.makedirs(src)
    for i in range(3):
        Image.new("RGB", (2400, 1200), (100, i * 60, 30)).save(
            os.path.join(src, f"{i+1:03d}.png"))
    plan = splitter.build_plan(src)
    written = splitter.execute_plan(plan, os.path.join(tmp, "split"), workers=2)
    todo = list(enumerate(os.path.join(src, n) for n in list_images(src)))
    res = cropper.batch_crop(
        todo, (1200, 1200), lambda i: 0,
        lambda i: os.path.join(tmp, "crop", f"{i:03d}.png"),
        cropper.BaseConfig(enabled=True, bw=1200, bh=1200))
    print(f"manga-splitter v{__version__} selftest")
    print(f"  分页: 输入 3 张 -> 输出 {len(written)} 张 (期望 6)")
    print(f"  裁剪: 成功 {res.done} / 失败 {res.failed} (期望 3/0)")
    ok = len(written) == 6 and res.done == 3 and res.failed == 0
    print("  RESULT:", "OK" if ok else "FAIL")
    # windowed exe 没有控制台，结果同时落盘，方便双击/脚本验证
    try:
        from .settings import app_dir
        with open(os.path.join(app_dir(), "selftest.log"), "w",
                  encoding="utf-8") as f:
            f.write(f"v{__version__} 分页输出 {len(written)} (期望6)  "
                    f"裁剪 {res.done} 失败 {res.failed} (期望 3/0)\n"
                    f"RESULT: {'OK' if ok else 'FAIL'}\n")
    except Exception:  # noqa: BLE001
        pass
    return 0 if ok else 1


def main() -> int:
    # onefile exe 启动时工作目录可能是临时目录，统一回程序目录
    try:
        from .settings import app_dir
        os.chdir(app_dir())
    except Exception:  # noqa: BLE001
        pass

    argv = sys.argv[1:]
    if "--version" in argv or "-V" in argv:
        from . import __version__
        print(f"漫画双页分割器 v{__version__}")
        return 0
    if "--selftest" in argv:
        return _selftest()
    if "--check-update" in argv:
        from . import __version__
        from .updater import check_latest
        info = check_latest()
        if not info.get("ok"):
            print(f"检查更新失败：{info.get('error')}")
            return 2
        print(f"当前 v{__version__}，最新 {info.get('tag')}，"
              f"{'有更新' if info.get('latest') else '已是最新'}")
        return 0

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("manga-splitter")

    from .ui.theme import QSS
    app.setStyleSheet(QSS)

    from .ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
