# -*- coding: utf-8 -*-
"""
主窗口：左侧导航 + QStackedWidget 双视图 + 自动更新入口。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget,
    QStackedWidget, QLabel, QMessageBox, QProgressDialog, QPushButton,
)

from .. import __version__, APP_NAME, UPDATE_REPO
from ..settings import Settings
from ..updater import (check_latest, download_asset, apply_update_and_restart,
                       cleanup_old_versions, UpdateError)
from .split_page import SplitPage
from .crop_page import CropPage
from .workers import FuncWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  v{__version__}")
        self.settings = Settings()
        cleanup_old_versions()

        central = QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 侧栏 ----
        side = QWidget()
        side.setFixedWidth(150)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.addItems(["自动分页", "框选裁剪"])
        self.nav.setCurrentRow(0)
        sv.addWidget(self.nav, 1)
        self.btn_update = QPushButton("检查更新")
        self.btn_update.clicked.connect(lambda: self.check_update(manual=True))
        sv.addWidget(self.btn_update)
        ver = QLabel(f"v{__version__}")
        ver.setAlignment(Qt.AlignCenter)
        ver.setProperty("muted", True)
        sv.addWidget(ver)
        root.addWidget(side)

        # ---- 页面 ----
        self.stack = QStackedWidget()
        self.split_page = SplitPage(self.settings)
        self.crop_page = CropPage(self.settings,
                                  out_name_fn=self.split_page.crop_out_name)
        self.stack.addWidget(self.split_page)
        self.stack.addWidget(self.crop_page)
        root.addWidget(self.stack, 1)

        self.nav.currentRowChanged.connect(self._on_nav)

        # 双向同步册列表
        self.crop_page.folders_changed.connect(self.split_page.set_folders)
        self.nav.currentRowChanged.connect(self._sync_into_crop)

        self._fit_geometry()

        self._update_worker: FuncWorker | None = None
        if self.settings.get("update.auto_check", True):
            self.check_update(manual=False)

    # ------------------------------------------------------------ 窗口

    def _fit_geometry(self):
        scr = self.screen().availableGeometry()
        w = max(1100, min(1560, int(scr.width() * 0.9)))
        h = max(680, min(940, int(scr.height() * 0.9)))
        geo = self.settings.get("window.geometry", None)
        self.resize(w, h)
        if geo:
            try:
                self.restoreGeometry(bytes.fromhex(geo))
            except Exception:  # noqa: BLE001
                pass

    def _on_nav(self, row: int):
        self.stack.setCurrentIndex(row)

    def _sync_into_crop(self, row: int):
        if row == 1:
            self.crop_page.set_folders(self.split_page.folders)

    # ------------------------------------------------------------ 更新

    def check_update(self, manual: bool = False):
        if self._update_worker is not None and self._update_worker.isRunning():
            if manual:
                QMessageBox.information(self, "提示", "正在检查更新…")
            return

        def _check(progress=None, cancel=None, log=None):
            return check_latest(UPDATE_REPO)

        w = FuncWorker(_check, parent=self)
        self._update_worker = w

        def done(info: dict):
            if not info.get("ok"):
                if manual:
                    QMessageBox.information(
                        self, "检查更新",
                        f"暂时无法检查更新（{info.get('error', '网络问题')}）。\n"
                        f"当前版本 v{__version__}。")
                return
            if info.get("latest"):
                self._prompt_update(info)
            elif manual:
                QMessageBox.information(self, "检查更新",
                                        f"已是最新版本（v{__version__}）。")

        w.finished_ok.connect(done)
        if manual:
            w.failed.connect(lambda tb: QMessageBox.information(
                self, "检查更新", f"检查失败：\n{tb[:500]}"))
        w.start()

    def _prompt_update(self, info: dict):
        notes = (info.get("notes") or "").strip()
        size_mb = (info.get("asset_size") or 0) / 1048576
        msg = (f"发现新版本 {info['tag']}（当前 v{info['current']}）\n"
               f"文件：{info.get('asset_name') or '未知'}"
               + (f"（{size_mb:.1f} MB）" if size_mb else ""))
        if notes:
            msg += f"\n\n更新说明：\n{notes[:600]}"
        if not info.get("asset_url"):
            msg += "\n\n⚠ 该版本没有可下载的 exe 附件。"
            QMessageBox.information(self, "发现新版本", msg)
            return
        r = QMessageBox.question(self, "发现新版本", msg + "\n\n立即下载并更新？")
        if r == QMessageBox.Yes:
            self._download_update(info)

    def _download_update(self, info: dict):
        from ..settings import app_dir
        dst = os.path.join(app_dir(), ".update_tmp", info["asset_name"] or "update.exe")

        dlg = QProgressDialog("正在下载新版本…", "取消", 0, 100, self)
        dlg.setWindowTitle("下载更新")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)

        w = FuncWorker(
            lambda progress=None, cancel=None, log=None: download_asset(
                info["asset_url"], dst,
                progress=(lambda got, total: progress(got, total, "")) if progress else None,
                cancel=cancel),
            parent=self)
        self._update_worker = w

        def on_prog(got, total, _n):
            if total:
                dlg.setValue(int(got / total * 100))
                dlg.setLabelText(
                    f"正在下载新版本… {got/1048576:.1f} / {total/1048576:.1f} MB")

        def done(path: str):
            dlg.close()
            r = QMessageBox.question(
                self, "下载完成",
                "新版本已下载完成。\n\n立即重启并应用更新？\n"
                "（选择「否」可稍后通过再次检查更新来安装）")
            if r == QMessageBox.Yes:
                try:
                    self._apply_and_exit(path)
                except Exception as e:  # noqa: BLE001
                    QMessageBox.critical(self, "更新失败", str(e))

        def fail(tb: str):
            dlg.close()
            if "已取消" not in tb:
                QMessageBox.critical(self, "下载失败", tb[:800])

        w.progress.connect(on_prog)
        w.finished_ok.connect(done)
        w.failed.connect(fail)
        dlg.canceled.connect(w.stop)
        w.start()
        dlg.show()

    def _apply_and_exit(self, new_exe: str):
        from PySide6.QtWidgets import QApplication
        self.save_all()
        apply_update_and_restart(new_exe)
        QApplication.instance().quit()

    # ------------------------------------------------------------ 关闭

    def save_all(self):
        self.split_page.save_settings()
        self.crop_page.save_settings()
        try:
            self.settings.set("window.geometry", bytes(self.saveGeometry().toHex()).decode())
        except Exception:  # noqa: BLE001
            pass
        self.settings.save()

    def closeEvent(self, e):
        # 有后台任务时先确认
        busy = False
        for w in (getattr(self.split_page, "worker", None),
                  getattr(self.crop_page, "_worker", None)):
            if w is not None and w.isRunning():
                busy = True
        if busy:
            r = QMessageBox.question(self, "确认", "任务仍在进行，确定退出？")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            for w in (getattr(self.split_page, "worker", None),
                      getattr(self.crop_page, "_worker", None)):
                if w is not None and w.isRunning():
                    w.stop()
                    w.wait(3000)
        self.save_all()
        super().closeEvent(e)
