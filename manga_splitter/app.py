# -*- coding: utf-8 -*-
"""应用入口：python -m manga_splitter 或打包后的 exe 都从这里启动。"""

from __future__ import annotations

import os
import sys


def main() -> int:
    # onefile exe 启动时工作目录可能是临时目录，统一回程序目录
    try:
        from .settings import app_dir
        os.chdir(app_dir())
    except Exception:  # noqa: BLE001
        pass

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
