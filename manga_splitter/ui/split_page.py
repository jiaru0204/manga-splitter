# -*- coding: utf-8 -*-
"""
视图一：自动分页（双页硬切 + 单页重命名）。

对应旧 splitter_gui.py 的 split 页，改进点：
  - 后台线程用 Qt 信号回报（不再手动泵 queue）；
  - 「停止」在册内也即时生效（core 层 cancel 回调）；
  - 写盘失败逐条上报（旧版线程池内异常会直接炸掉整个任务）；
  - 结果预览带缩略图缓存，翻页不重复解码。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton, QLabel,
    QLineEdit, QCheckBox, QTreeWidget, QTreeWidgetItem, QProgressBar,
    QPlainTextEdit, QFileDialog, QMessageBox, QDialog, QSplitter,
)

from ..core.imaging import list_images
from ..core.naming import render_output_name, extract_volume_number
from ..core.splitter import build_plan, execute_plan, default_workers
from .workers import FuncWorker


class SplitPage(QWidget):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.folders: list[str] = []
        self.worker: FuncWorker | None = None
        self.last_out_dir: str = ""
        self._build_ui()
        self._restore()

    # ------------------------------------------------------------ UI

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        # ---- 左：待处理文件夹 ----
        gb_f = QGroupBox("1. 待处理文件夹", property_="card")
        gb_f.setProperty("class", "card")
        v = QVBoxLayout(gb_f)
        row = QHBoxLayout()
        self.btn_add = QPushButton("＋ 添加文件夹")
        self.btn_add.clicked.connect(self.add_folders)
        self.btn_add_sub = QPushButton("＋ 父目录全部子目录")
        self.btn_add_sub.clicked.connect(self.add_subdirs)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self.clear_folders)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_add_sub)
        row.addWidget(self.btn_clear)
        row.addStretch(1)
        self.count_lbl = QLabel("0 个")
        self.count_lbl.setProperty("muted", True)
        row.addWidget(self.count_lbl)
        v.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["#", "文件夹", "张数"])
        self.tree.setColumnWidth(0, 36)
        self.tree.setColumnWidth(2, 50)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        v.addWidget(self.tree, 1)
        split.addWidget(gb_f)

        # ---- 右：输出设置 ----
        gb_o = QGroupBox("2. 输出设置")
        gb_o.setProperty("class", "card")
        v = QVBoxLayout(gb_o)

        row = QHBoxLayout()
        row.addWidget(QLabel("输出根目录"))
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("留空 = 每册同级生成 <册名>_split")
        row.addWidget(self.out_edit, 1)
        b = QPushButton("选择")
        b.clicked.connect(self.choose_out)
        row.addWidget(b)
        v.addLayout(row)

        self.use_out_chk = QCheckBox("使用上面的输出根目录（每册在其下建子目录）")
        v.addWidget(self.use_out_chk)

        row = QHBoxLayout()
        row.addWidget(QLabel("命名模板"))
        self.tpl_edit = QLineEdit()
        self.tpl_edit.setPlaceholderText("用 # 代表卷号（## = 补零两位）；留空用原文件夹名")
        row.addWidget(self.tpl_edit, 1)
        b = QPushButton("清空")
        b.clicked.connect(self.tpl_edit.clear)
        row.addWidget(b)
        v.addLayout(row)

        self.tpl_preview = QLabel("（未启用模板）→ 预览：<册名>_split")
        self.tpl_preview.setProperty("accent", True)
        self.tpl_preview.setWordWrap(True)
        v.addWidget(self.tpl_preview)
        v.addStretch(1)
        split.addWidget(gb_o)
        split.setSizes([560, 480])

        self.tpl_edit.textChanged.connect(self._update_tpl_preview)
        self.use_out_chk.toggled.connect(self._update_tpl_preview)

        # ---- 操作行 ----
        row = QHBoxLayout()
        self.btn_scan = QPushButton("预扫描（只分析）")
        self.btn_scan.clicked.connect(lambda: self._start("scan"))
        self.btn_run = QPushButton("开始分割")
        self.btn_run.setProperty("accent", True)
        self.btn_run.clicked.connect(lambda: self._start("run"))
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        row.addWidget(self.btn_scan)
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        b = QPushButton("预览结果")
        b.clicked.connect(self.show_preview)
        row.addWidget(b)
        b = QPushButton("打开输出目录")
        b.clicked.connect(self.open_out)
        row.addWidget(b)
        root.addLayout(row)

        # ---- 进度 + 日志 ----
        gb_p = QGroupBox("3. 进度与日志")
        gb_p.setProperty("class", "card")
        v = QVBoxLayout(gb_p)
        self.prog = QProgressBar()
        v.addWidget(self.prog)
        self.status_lbl = QLabel("就绪")
        self.status_lbl.setProperty("muted", True)
        v.addWidget(self.status_lbl)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)
        root.addWidget(gb_p, 2)

    # ------------------------------------------------------------ 配置持久化

    def _restore(self):
        self.out_edit.setText(self.settings.get("split.out_root", ""))
        self.use_out_chk.setChecked(bool(self.settings.get("split.use_out_root", False)))
        self.tpl_edit.setText(self.settings.get("split.template", ""))
        for f in self.settings.get("split.folders", []):
            if os.path.isdir(f):
                self._append_folder(f, quiet=True)
        self._update_tpl_preview()

    def save_settings(self):
        self.settings.set("split.out_root", self.out_edit.text().strip())
        self.settings.set("split.use_out_root", self.use_out_chk.isChecked())
        self.settings.set("split.template", self.tpl_edit.text().strip())
        self.settings.set("split.folders", list(self.folders))

    # ------------------------------------------------------------ 文件夹

    def add_folders(self):
        d = QFileDialog.getExistingDirectory(self, "选择漫画册文件夹")
        if d:
            self._append_folder(d)

    def add_subdirs(self):
        d = QFileDialog.getExistingDirectory(self, "选择父目录（其下所有子目录都将被处理）")
        if not d:
            return
        subs = [os.path.join(d, n) for n in sorted(os.listdir(d))
                if os.path.isdir(os.path.join(d, n))]
        if not subs:
            QMessageBox.information(self, "提示", "该目录下没有子目录。")
            return
        for s in subs:
            self._append_folder(s, quiet=True)
        self._log(f"[添加] 父目录 {d} 下 {len(subs)} 个子目录")

    def _append_folder(self, path: str, quiet: bool = False):
        path = os.path.normpath(path)
        if path in self.folders:
            return
        self.folders.append(path)
        n = len(list_images(path))
        it = QTreeWidgetItem([str(len(self.folders)), path, str(n)])
        self.tree.addTopLevelItem(it)
        self.count_lbl.setText(f"{len(self.folders)} 个")
        if not quiet:
            self._log(f"[添加] {path}  (含 {n} 张图片)")
        self._update_tpl_preview()

    def clear_folders(self):
        self.folders.clear()
        self.tree.clear()
        self.count_lbl.setText("0 个")
        self._update_tpl_preview()

    def set_folders(self, folders: list[str]):
        """由裁剪页同步过来的册列表。"""
        new = [os.path.normpath(f) for f in folders if f]
        if new == self.folders:
            return
        self.folders = []
        self.tree.clear()
        for f in new:
            self._append_folder(f, quiet=True)

    # ------------------------------------------------------------ 命名

    def _tpl(self) -> str:
        return self.tpl_edit.text().strip()

    def _update_tpl_preview(self):
        tpl = self._tpl()
        if not tpl:
            self.tpl_preview.setText("（未启用模板）→ 预览：<册名>_split")
            return
        sample = (os.path.basename(os.path.normpath(self.folders[0]))
                  if self.folders else "Lm-dcb Vol_04")
        if extract_volume_number(sample) is None:
            self.tpl_preview.setText(f"⚠ 示例「{sample}」里没有数字，无法套用模板")
            return
        self.tpl_preview.setText(f"预览：{sample}  →  {render_output_name(sample, tpl)}")

    def resolve_out_dir(self, folder: str, suffix: str = "_split") -> str:
        base = os.path.basename(os.path.normpath(folder))
        tpl = self._tpl()
        root = self.out_edit.text().strip()
        use_root = self.use_out_chk.isChecked() and bool(root)
        if tpl:
            name = render_output_name(base, tpl)
        else:
            name = base if use_root else base + suffix
        if use_root:
            return os.path.join(root, name)
        return os.path.join(os.path.dirname(os.path.normpath(folder)), name)

    def crop_out_name(self, folder: str) -> str:
        """裁剪页默认输出目录名（与旧版 _crop_name_fn 行为一致）。"""
        base = os.path.basename(os.path.normpath(folder))
        tpl = self._tpl()
        return render_output_name(base, tpl) if tpl else base + "_crop"

    def choose_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出根目录")
        if d:
            self.out_edit.setText(d)
            self.use_out_chk.setChecked(True)
            self._update_tpl_preview()

    # ------------------------------------------------------------ 任务

    def _log(self, text: str):
        self.log.appendPlainText(text)

    def _start(self, mode: str):
        if not self.folders:
            QMessageBox.warning(self, "提示", "请先添加至少一个文件夹。")
            return
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "提示", "任务正在进行中。")
            return
        if mode == "run":
            r = QMessageBox.question(
                self, "确认",
                f"即将处理 {len(self.folders)} 个文件夹，输出为 PNG。\n\n继续？")
            if r != QMessageBox.Yes:
                return
        self.save_settings()
        self.settings.save()
        self.prog.setValue(0)
        self.last_out_dir = ""
        self.btn_run.setEnabled(False)
        self.btn_scan.setEnabled(False)
        self.btn_stop.setEnabled(True)

        folders = list(self.folders)
        out_of = {f: self.resolve_out_dir(f) for f in folders}
        self.worker = FuncWorker(self._work, folders, mode, out_of, parent=self)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self._log)
        self.worker.finished_ok.connect(lambda _r: self._on_done(False))
        self.worker.failed.connect(self._on_fail)
        self.worker.start()

    def _stop(self):
        if self.worker is not None:
            self.worker.stop()
            self._log("[停止] 已请求停止，等待当前图片处理完成…")

    @staticmethod
    def _work(folders, mode, out_of, progress=None, cancel=None, log=None):
        """在子线程执行；通过注入的回调回报（由 FuncWorker 注入）。"""
        stopped = False
        for fi, folder in enumerate(folders, 1):
            if cancel and cancel():
                log("[停止] 用户中止。")
                stopped = True
                break
            name = os.path.basename(os.path.normpath(folder))
            log(f"\n===== [{fi}/{len(folders)}] {name} =====")

            plan = build_plan(folder, progress=progress, cancel=cancel)
            if plan.cancelled:
                log("[停止] 用户中止。")
                stopped = True
                break
            log(f"  基准宽 = {plan.base_width}px  |  单页 {plan.single_count} 张，"
                f"双页 {plan.double_count} 张  →  输出 {len(plan.items)} 张")
            if plan.wide_files:
                log(f"  [超宽图 {len(plan.wide_files)} 张，已按规则切片]")
                for wf in plan.wide_files:
                    log(f"     - {wf}")
            for e in plan.errors:
                log(f"  [读取失败] {e}")

            if mode == "scan":
                log("  （预扫描模式，未写盘）")
                continue

            out_dir = out_of[folder]
            log(f"  → 输出到 {out_dir}")
            nw = default_workers(len(plan.items))
            if nw > 1:
                log(f"    并行编码：{nw} 线程")
            written = execute_plan(plan, out_dir, progress=progress, cancel=cancel)
            for e in plan.write_errors:
                log(f"  [写出失败] {e}")
            if plan.cancelled:
                log(f"  [停止] 已写出 {len(written)} 张（中断）")
                stopped = True
                break
            log(f"  ✔ 完成，共写出 {len(written)} 张 PNG")
        return {"stopped": stopped}

    def _on_progress(self, done: int, total: int, _name: str):
        pct = int(done / total * 100) if total else 0
        self.prog.setValue(pct)
        self.status_lbl.setText(f"处理中 {done}/{total}  ({pct:.1f}%)")

    def _on_done(self, stopped: bool):
        self.btn_run.setEnabled(True)
        self.btn_scan.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.status_lbl.setText("已停止" if stopped else "全部完成")
        self._log("[结束] " + ("任务中断。" if stopped else "全部完成。"))
        # 记录最后一个输出目录
        for f in reversed(self.folders):
            for sfx in ("_split", "_crop"):
                cand = self.resolve_out_dir(f, sfx)
                if os.path.isdir(cand):
                    self.last_out_dir = cand
                    return

    def _on_fail(self, tb: str):
        self._log("[异常]\n" + tb)
        self._on_done(True)

    # ------------------------------------------------------------ 预览

    def _latest_out_dir(self) -> str:
        if self.last_out_dir and os.path.isdir(self.last_out_dir):
            return self.last_out_dir
        for f in self.folders:
            for sfx in ("_split", "_crop"):
                cand = self.resolve_out_dir(f, sfx)
                if os.path.isdir(cand):
                    return cand
        return ""

    def show_preview(self):
        out_dir = self._latest_out_dir()
        if not out_dir:
            QMessageBox.information(self, "提示", "尚未生成输出，或输出目录不存在。")
            return
        PreviewDialog(out_dir, self).exec()

    def open_out(self):
        d = self._latest_out_dir()
        if not d:
            QMessageBox.information(self, "提示", "输出目录不存在。")
            return
        os.startfile(os.path.normpath(d))  # type: ignore[attr-defined]


class PreviewDialog(QDialog):
    """输出结果预览：翻页浏览 PNG，带缩略图缓存（旧版每次翻页都重新解码）。"""

    def __init__(self, out_dir: str, parent=None):
        super().__init__(parent)
        self.out_dir = out_dir
        self.names = sorted(n for n in os.listdir(out_dir)
                            if n.lower().endswith(".png"))
        self.idx = 0
        self._cache: dict[int, QPixmap] = {}

        self.setWindowTitle(f"预览 - {out_dir}")
        self.resize(900, 700)
        v = QVBoxLayout(self)

        row = QHBoxLayout()
        self.info = QLabel()
        row.addWidget(self.info)
        row.addStretch(1)
        b = QPushButton("◀ 上一张")
        b.clicked.connect(self.prev)
        row.addWidget(b)
        b = QPushButton("下一张 ▶")
        b.clicked.connect(self.next)
        row.addWidget(b)
        v.addLayout(row)

        self.img_lbl = QLabel(alignment=Qt.AlignCenter)
        self.img_lbl.setMinimumSize(400, 300)
        self.img_lbl.setStyleSheet("background:#c9d0da; border-radius:8px;")
        v.addWidget(self.img_lbl, 1)

        if not self.names:
            self.info.setText("目录中没有 PNG")
            return
        self._render()

    def _render(self):
        i = self.idx
        if i not in self._cache:
            pm = QPixmap(os.path.join(self.out_dir, self.names[i]))
            self._cache[i] = pm
            if len(self._cache) > 12:      # 简单 LRU：清掉最旧的一半
                for k in sorted(self._cache)[:6]:
                    if k != i:
                        self._cache.pop(k, None)
        pm = self._cache[i]
        scaled = pm.scaled(self.img_lbl.size(), Qt.KeepAspectRatio,
                           Qt.SmoothTransformation)
        self.img_lbl.setPixmap(scaled)
        self.info.setText(f"[{i+1}/{len(self.names)}] {self.names[i]}   "
                          f"{pm.width()}x{pm.height()}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.names:
            self._render()

    def prev(self):
        if self.names:
            self.idx = (self.idx - 1) % len(self.names)
            self._render()

    def next(self):
        if self.names:
            self.idx = (self.idx + 1) % len(self.names)
            self._render()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Left:
            self.prev()
        elif e.key() == Qt.Key_Right:
            self.next()
        else:
            super().keyPressEvent(e)
