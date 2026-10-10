# -*- coding: utf-8 -*-
"""
视图二：框选裁剪（按指定分辨率截取）。

功能与旧 crop_panel.py 完全对齐，但实现方式全面升级：
  - 画布换成 QGraphicsView（见 crop_view.py），滚轮缩放/平移零手工换算；
  - 批量裁剪 / 批量补白跑在后台线程，带进度框、可取消、失败逐条上报
    （旧版批量裁剪卡 UI 线程，且 except: pass 静默吞错）；
  - 序号/后缀分配抽成 core.cropper.SeqAssigner，状态可快照恢复；
  - 所有设置随 settings.json 持久化，重开不丢。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QTimer
from PySide6.QtGui import QColor, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton, QLabel,
    QLineEdit, QCheckBox, QRadioButton, QComboBox, QTreeWidget, QTreeWidgetItem,
    QFileDialog, QMessageBox, QScrollArea, QSplitter, QColorDialog,
    QProgressDialog,
)

from ..core.cropper import (CropSpec, BaseConfig, SeqAssigner, render_crop,
                            batch_crop, batch_add_base, BatchResult)
from ..core.imaging import list_images, read_sizes
from ..core.naming import render_output_filename, suffix_at, DEFAULT_NAME_TPL
from .crop_view import CropView, HANDLE_R, MIN_SIZE
from . import theme
from .workers import FuncWorker

PRESET_COLORS = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "gray": (128, 128, 128),
}


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def _parse_px(s) -> int:
    try:
        return max(0, int(float((s or "").strip())))
    except Exception:  # noqa: BLE001
        return 0


class CropPage(QWidget):
    folders_changed = Signal(list)

    def __init__(self, settings, out_name_fn=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.out_name_fn = out_name_fn   # folder -> 默认输出目录名（来自分页页模板）

        # ---- 册队列与进度 ----
        self.folders: list[str] = []
        self.folder_idx = 0
        self.files: list[str] = []
        self.idx = 0
        self.folder_state: dict[str, dict] = {}
        self._loaded_folder: str | None = None

        # ---- 图片状态 ----
        self.pix_orig = QPixmap()
        self.pix = QPixmap()
        self.rot = 0
        self.rot_map: dict[int, int] = {}
        self.assigner = SeqAssigner()
        self._sizes: dict[int, tuple[int, int] | None] = {}
        self.saved_count = 0
        self._last_saved = ""
        self._used_paths: set[str] = set()

        # ---- 选框 ----
        self.box = [0, 0, 100, 100]
        self.fixed_size: tuple[int, int] | None = None
        self.align_mode = "left"
        self._drag_mode: str | None = None
        self._drag_start = QPointF()
        self._drag_box0 = (0, 0, 0, 0)
        self._pending_outside: QPointF | None = None
        self.show_overlay = True

        # ---- 取色 ----
        self._picking = False
        self._eyedrop_side = "base"
        self.custom_rgb = (255, 255, 255)
        self.left_rgb = (255, 255, 255)
        self.right_rgb = (255, 255, 255)

        self._base_warned = False
        self._worker: FuncWorker | None = None

        self._build_ui()
        self._build_shortcuts()
        self._restore()

    # ================================================================ UI

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        # ---- 顶：册切换 ----
        top = QHBoxLayout()
        top.addWidget(self._bold("当前册"))
        self.title_lbl = QLabel("")
        self.title_lbl.setProperty("accent", True)
        top.addWidget(self.title_lbl, 1)
        b = QPushButton("＋ 添加册")
        b.clicked.connect(self.add_folder)
        top.addWidget(b)
        b = QPushButton("＋ 父目录全部")
        b.clicked.connect(self.add_subdirs)
        top.addWidget(b)
        b = QPushButton("移除")
        b.clicked.connect(self.remove_folder)
        top.addWidget(b)
        self.folder_box = QComboBox()
        self.folder_box.setMinimumWidth(240)
        self.folder_box.currentIndexChanged.connect(self._on_folder_box)
        top.addWidget(self.folder_box)
        self.btn_prev_folder = QPushButton("◀")
        self.btn_prev_folder.setFixedWidth(34)
        self.btn_prev_folder.clicked.connect(self.prev_folder)
        top.addWidget(self.btn_prev_folder)
        self.btn_next_folder = QPushButton("▶")
        self.btn_next_folder.setFixedWidth(34)
        self.btn_next_folder.clicked.connect(self.next_folder)
        top.addWidget(self.btn_next_folder)
        root.addLayout(top)

        # ---- 三栏 ----
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        # 左：图片列表
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("图片列表")
        gb.setProperty("class", "card")
        gvl = QVBoxLayout(gb)
        row = QHBoxLayout()
        row.addWidget(QLabel("跳到第"))
        self.goto_edit = QLineEdit()
        self.goto_edit.setFixedWidth(56)
        self.goto_edit.returnPressed.connect(self.goto_seq)
        row.addWidget(self.goto_edit)
        row.addWidget(QLabel("张"))
        b = QPushButton("跳")
        b.setFixedWidth(36)
        b.clicked.connect(self.goto_seq)
        row.addWidget(b)
        row.addStretch(1)
        self.count_lbl = QLabel("")
        self.count_lbl.setProperty("muted", True)
        row.addWidget(self.count_lbl)
        gvl.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("显示"))
        self.filter_box = QComboBox()
        self.filter_box.addItems(["全部", "只看超出", "只看可补白"])
        self.filter_box.currentTextChanged.connect(lambda _t: self._populate_list())
        row.addWidget(self.filter_box)
        row.addStretch(1)
        self.thr_lbl = QLabel("")
        self.thr_lbl.setProperty("muted", True)
        row.addWidget(self.thr_lbl)
        gvl.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["输出", "#", "文件名", "尺寸", "角度"])
        self.tree.setColumnWidth(0, 64)
        self.tree.setColumnWidth(1, 40)
        self.tree.setColumnWidth(3, 96)
        self.tree.setColumnWidth(4, 44)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.currentItemChanged.connect(self._on_tree_select)
        gvl.addWidget(self.tree, 1)
        lv.addWidget(gb)
        split.addWidget(left)

        # 中：画布
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(self._bold("拖动选框选择要保留的区域（框内保留）"))
        self.overlay_chk = QCheckBox("显示选框")
        self.overlay_chk.setChecked(True)
        self.overlay_chk.toggled.connect(self._on_overlay_chk)
        row.addWidget(self.overlay_chk)
        hint = QLabel("点框外隐藏 · 点框内显示 · Ctrl+H")
        hint.setProperty("muted", True)
        row.addWidget(hint)
        row.addStretch(1)
        self.canvas_hint = QLabel("滚轮缩放 · 右键还原视图")
        self.canvas_hint.setProperty("muted", True)
        row.addWidget(self.canvas_hint)
        cv.addLayout(row)
        self.view = CropView()
        self.view.pressed.connect(self._on_press)
        self.view.dragged.connect(self._on_drag)
        self.view.released.connect(self._on_release)
        self.view.fit_requested.connect(self._fit_view)
        cv.addWidget(self.view, 1)
        split.addWidget(center)

        # 右：控制面板（滚动区）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(360)
        scroll.setMaximumWidth(430)
        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 6, 0)
        pv.setSpacing(8)
        self._build_size_card(pv)
        self._build_rotate_card(pv)
        self._build_base_card(pv)
        self._build_output_card(pv)
        self._build_naming_card(pv)
        pv.addStretch(1)
        scroll.setWidget(panel)
        split.addWidget(scroll)
        split.setSizes([300, 640, 380])

        # ---- 底：信息 + 操作 ----
        bottom = QHBoxLayout()
        self.info_lbl = QLabel("")
        self.info_lbl.setProperty("muted", True)
        bottom.addWidget(self.info_lbl, 1)
        b = QPushButton("◀ 上一张")
        b.clicked.connect(self.prev)
        bottom.addWidget(b)
        b = QPushButton("下一张 ▶")
        b.clicked.connect(self.next)
        bottom.addWidget(b)
        self.apply_rest_chk = QCheckBox("套用到后续")
        bottom.addWidget(self.apply_rest_chk)
        b = QPushButton("跳过")
        b.clicked.connect(lambda: self._load(self.idx + 1))
        bottom.addWidget(b)
        b = QPushButton("本图再截一块")
        b.clicked.connect(self.save_only)
        bottom.addWidget(b)
        b = QPushButton("全部套用并批量保存")
        b.setProperty("ok", True)
        b.clicked.connect(lambda: self.batch_apply(from_current=True))
        bottom.addWidget(b)
        b = QPushButton("保存并下一张")
        b.setProperty("accent", True)
        b.clicked.connect(self.save_next)
        bottom.addWidget(b)
        root.addLayout(bottom)

    @staticmethod
    def _bold(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setStyleSheet("font-weight: bold;")
        return lb

    def _card(self, layout, title: str) -> QVBoxLayout:
        gb = QGroupBox(title)
        gb.setProperty("class", "card")
        v = QVBoxLayout(gb)
        layout.addWidget(gb)
        return v

    def _build_size_card(self, pv):
        v = self._card(pv, "截取尺寸（像素）")
        row = QHBoxLayout()
        row.addWidget(QLabel("宽"))
        self.w_edit = QLineEdit()
        self.w_edit.setFixedWidth(70)
        row.addWidget(self.w_edit)
        row.addWidget(QLabel("高"))
        self.h_edit = QLineEdit()
        self.h_edit.setFixedWidth(70)
        row.addWidget(self.h_edit)
        b = QPushButton("应用")
        b.clicked.connect(self.apply_size)
        row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        row = QHBoxLayout()
        for text, fn in (("占满宽", lambda: self.quick(1.0, None)),
                         ("一半宽", lambda: self.quick(0.5, None)),
                         ("整图", self.fit_whole)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("选框靠"))
        for text, mode in (("左", "left"), ("中", "center"), ("右", "right")):
            b = QPushButton(text)
            b.setFixedWidth(40)
            b.clicked.connect(lambda _c=False, m=mode: self.align(m))
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.auto_apply_chk = QCheckBox("换图时自动套用此尺寸")
        self.auto_apply_chk.setChecked(True)
        self.auto_apply_chk.toggled.connect(self._on_auto_apply_toggle)
        v.addWidget(self.auto_apply_chk)
        self.size_hint = QLabel("")
        self.size_hint.setWordWrap(True)
        v.addWidget(self.size_hint)

    def _build_rotate_card(self, pv):
        v = self._card(pv, "旋转")
        row = QHBoxLayout()
        for text, fn, w in (("左转", lambda: self.rotate(-90), 52),
                            ("右转", lambda: self.rotate(90), 52),
                            ("180°", lambda: self.rotate(180), 56),
                            ("复位", self.rotate_reset, 52)):
            b = QPushButton(text)
            b.setFixedWidth(w)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addWidget(QLabel("当前"))
        self.rot_lbl = QLabel("0°")
        self.rot_lbl.setProperty("accent", True)
        self.rot_lbl.setStyleSheet("font-weight: bold; font-size: 15px;")
        row.addWidget(self.rot_lbl)
        row.addStretch(1)
        v.addLayout(row)
        b = QPushButton("把当前角度套用到本册其余图片")
        b.clicked.connect(self.rotate_apply_rest)
        v.addWidget(b)

    def _build_base_card(self, pv):
        v = self._card(pv, "基底（统一输出分辨率）")
        row = QHBoxLayout()
        self.base_on_chk = QCheckBox("启用")
        self.base_on_chk.toggled.connect(self._on_base_toggle)
        row.addWidget(self.base_on_chk)
        row.addWidget(QLabel("尺寸"))
        self.base_w_edit = QLineEdit()
        self.base_w_edit.setFixedWidth(66)
        self.base_w_edit.textChanged.connect(self._on_base_size_changed)
        row.addWidget(self.base_w_edit)
        row.addWidget(QLabel("×"))
        self.base_h_edit = QLineEdit()
        self.base_h_edit.setFixedWidth(66)
        self.base_h_edit.textChanged.connect(self._on_base_size_changed)
        row.addWidget(self.base_h_edit)
        b = QPushButton("取当前")
        b.clicked.connect(self.base_fill_current)
        row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("底色"))
        self.color_mode_group: list[QRadioButton] = []
        for text, val in (("白", "white"), ("黑", "black"), ("灰", "gray"), ("自定", "custom")):
            rb = QRadioButton(text)
            rb.setProperty("cval", val)
            rb.toggled.connect(self._on_color_change)
            if val == "white":
                rb.setChecked(True)
            self.color_mode_group.append(rb)
            row.addWidget(rb)
        self.swatch = QLabel()
        self.swatch.setFixedSize(22, 22)
        self.swatch.setStyleSheet("border:1px solid #d8dee9; background:#ffffff;")
        row.addWidget(self.swatch)
        b = QPushButton("调色")
        b.setFixedWidth(48)
        b.clicked.connect(self.pick_color_dialog)
        row.addWidget(b)
        self.btn_eyedrop = QPushButton("取色")
        self.btn_eyedrop.setFixedWidth(48)
        self.btn_eyedrop.clicked.connect(lambda: self.start_eyedrop("base"))
        row.addWidget(self.btn_eyedrop)
        row.addStretch(1)
        v.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("左补"))
        self.pad_l_edit = QLineEdit()
        self.pad_l_edit.setFixedWidth(56)
        self.pad_l_edit.textChanged.connect(self._sync_base_w_from_pads)
        row.addWidget(self.pad_l_edit)
        lb = QLabel("px")
        lb.setProperty("muted", True)
        row.addWidget(lb)
        row.addWidget(QLabel("右补"))
        self.pad_r_edit = QLineEdit()
        self.pad_r_edit.setFixedWidth(56)
        self.pad_r_edit.textChanged.connect(self._sync_base_w_from_pads)
        row.addWidget(self.pad_r_edit)
        lb = QLabel("px")
        lb.setProperty("muted", True)
        row.addWidget(lb)
        row.addStretch(1)
        v.addLayout(row)

        lb = QLabel("填了左右补 → 基底宽自动 = 内容宽 + 左 + 右（高仍按上面的填）")
        lb.setProperty("muted", True)
        lb.setWordWrap(True)
        v.addWidget(lb)

        row = QHBoxLayout()
        self.sep_chk = QCheckBox("左右补不同色")
        self.sep_chk.toggled.connect(self._on_color_change)
        row.addWidget(self.sep_chk)
        self.swatch_l = QLabel()
        self.swatch_l.setFixedSize(20, 20)
        row.addWidget(self.swatch_l)
        b = QPushButton("左")
        b.setFixedWidth(34)
        b.clicked.connect(lambda: self.pick_side_color("left"))
        row.addWidget(b)
        b = QPushButton("吸")
        b.setFixedWidth(34)
        b.clicked.connect(lambda: self.start_eyedrop("left"))
        row.addWidget(b)
        self.swatch_r = QLabel()
        self.swatch_r.setFixedSize(20, 20)
        row.addWidget(self.swatch_r)
        b = QPushButton("右")
        b.setFixedWidth(34)
        b.clicked.connect(lambda: self.pick_side_color("right"))
        row.addWidget(b)
        b = QPushButton("吸")
        b.setFixedWidth(34)
        b.clicked.connect(lambda: self.start_eyedrop("right"))
        row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self._update_side_swatches()

        row = QHBoxLayout()
        b = QPushButton("给已裁成品批量补白…")
        b.clicked.connect(self.batch_add_base)
        row.addWidget(b)
        lb = QLabel("选文件夹统一补齐")
        lb.setProperty("muted", True)
        row.addWidget(lb)
        row.addStretch(1)
        v.addLayout(row)

    def _build_output_card(self, pv):
        v = self._card(pv, "输出位置")
        self.follow_src_chk = QCheckBox("存回每张图的原文件夹")
        self.follow_src_chk.setChecked(True)
        self.follow_src_chk.toggled.connect(lambda _c: self._update_visuals())
        v.addWidget(self.follow_src_chk)
        row = QHBoxLayout()
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("留空 = 跟随上面的规则")
        row.addWidget(self.out_edit, 1)
        b = QPushButton("选择")
        b.setFixedWidth(44)
        b.clicked.connect(self.choose_out_dir)
        row.addWidget(b)
        b = QPushButton("清空")
        b.setFixedWidth(44)
        b.clicked.connect(self.out_edit.clear)
        row.addWidget(b)
        b = QPushButton("打开")
        b.setFixedWidth(44)
        b.clicked.connect(self.open_out_dir)
        row.addWidget(b)
        v.addLayout(row)

    def _build_naming_card(self, pv):
        v = self._card(pv, "输出命名")
        row = QHBoxLayout()
        row.addWidget(QLabel("模板"))
        self.name_tpl_edit = QLineEdit(DEFAULT_NAME_TPL)
        self.name_tpl_edit.textChanged.connect(self._update_name_preview)
        row.addWidget(self.name_tpl_edit, 1)
        b = QPushButton("默认")
        b.setFixedWidth(44)
        b.clicked.connect(lambda: self.name_tpl_edit.setText(DEFAULT_NAME_TPL))
        row.addWidget(b)
        v.addLayout(row)
        lb = QLabel("默认 = 当前图片名（原图 084.jpg → 输出 084.png）")
        lb.setProperty("muted", True)
        v.addWidget(lb)
        lb = QLabel("{n}=序号  {a}=同图块(a/b/c)  {name}=原图名  {vol}=卷号  {n:03}=补零3位")
        lb.setProperty("muted", True)
        lb.setWordWrap(True)
        v.addWidget(lb)
        self.name_preview = QLabel("")
        self.name_preview.setProperty("accent", True)
        v.addWidget(self.name_preview)

    # ================================================================ 快捷键

    def _build_shortcuts(self):
        self._shortcuts = []

        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
            self._shortcuts.append(s)

        sc(Qt.Key_Left, self.prev)
        sc(Qt.Key_Right, self.next)
        sc("Ctrl+S", self.save_next)
        sc("Ctrl+R", lambda: self.rotate(90))
        sc("Ctrl+L", lambda: self.rotate(-90))
        sc("Ctrl+0", self.rotate_reset)
        sc("Ctrl+H", self.toggle_overlay)
        sc(Qt.Key_Escape, self._on_escape)

    # ================================================================ 配置持久化

    def _restore(self):
        g = self.settings.get
        self.follow_src_chk.setChecked(bool(g("crop.follow_src", True)))
        self.out_edit.setText(g("crop.out_dir", ""))
        self.name_tpl_edit.setText(g("crop.name_tpl", DEFAULT_NAME_TPL))
        self.base_on_chk.setChecked(bool(g("crop.base_on", False)))
        self.base_w_edit.setText(str(g("crop.base_w", "") or ""))
        self.base_h_edit.setText(str(g("crop.base_h", "") or ""))
        self.pad_l_edit.setText(str(g("crop.pad_l", "") or ""))
        self.pad_r_edit.setText(str(g("crop.pad_r", "") or ""))
        self.sep_chk.setChecked(bool(g("crop.sep_colors", False)))
        self.auto_apply_chk.setChecked(bool(g("crop.auto_apply", True)))
        self.apply_rest_chk.setChecked(bool(g("crop.apply_rest", False)))
        fs = g("crop.fixed_size", None)
        if isinstance(fs, (list, tuple)) and len(fs) == 2:
            self.fixed_size = (int(fs[0]), int(fs[1]))
        self.align_mode = g("crop.align_mode", "left")
        mode = g("crop.color_mode", "white")
        for rb in self.color_mode_group:
            rb.blockSignals(True)
            rb.setChecked(rb.property("cval") == mode)
            rb.blockSignals(False)
        self.custom_rgb = tuple(g("crop.custom_rgb", [255, 255, 255]))
        self.left_rgb = tuple(g("crop.left_rgb", [255, 255, 255]))
        self.right_rgb = tuple(g("crop.right_rgb", [255, 255, 255]))
        self._update_swatch()
        self._update_side_swatches()

    def save_settings(self):
        s = self.settings.set
        s("crop.follow_src", self.follow_src_chk.isChecked())
        s("crop.out_dir", self.out_edit.text().strip())
        s("crop.name_tpl", self.name_tpl_edit.text())
        s("crop.base_on", self.base_on_chk.isChecked())
        s("crop.base_w", self.base_w_edit.text().strip())
        s("crop.base_h", self.base_h_edit.text().strip())
        s("crop.pad_l", self.pad_l_edit.text().strip())
        s("crop.pad_r", self.pad_r_edit.text().strip())
        s("crop.sep_colors", self.sep_chk.isChecked())
        s("crop.auto_apply", self.auto_apply_chk.isChecked())
        s("crop.apply_rest", self.apply_rest_chk.isChecked())
        s("crop.fixed_size", list(self.fixed_size) if self.fixed_size else None)
        s("crop.align_mode", self.align_mode)
        s("crop.color_mode", self._color_mode())
        s("crop.custom_rgb", list(self.custom_rgb))
        s("crop.left_rgb", list(self.left_rgb))
        s("crop.right_rgb", list(self.right_rgb))

    # ================================================================ 册管理

    def set_folders(self, folders: list[str]):
        new = [os.path.normpath(f) for f in folders if f]
        if new == self.folders:
            return
        self.folders = new
        if not new:
            self._loaded_folder = None
            self.files = []
            self.pix = QPixmap()
            self.pix_orig = QPixmap()
            self.view.set_pixmap(QPixmap())
            self._refresh_folder_box()
            self._populate_list()
            self.info_lbl.setText("还没有添加文件夹 —— 请到「自动分页」页或上方添加")
            return
        self.folder_state = {k: v for k, v in self.folder_state.items() if k in new}
        self._switch_folder(0)

    def _folder_label(self, i: int) -> str:
        return f"{i+1}/{len(self.folders)}  {os.path.basename(self.folders[i])}"

    def _refresh_folder_box(self):
        self.folder_box.blockSignals(True)
        self.folder_box.clear()
        self.folder_box.addItems([self._folder_label(i) for i in range(len(self.folders))])
        if self.folders:
            self.folder_box.setCurrentIndex(self.folder_idx)
        self.folder_box.blockSignals(False)
        self._update_folder_buttons()

    def _on_folder_box(self, i: int):
        if 0 <= i < len(self.folders) and i != self.folder_idx:
            self._switch_folder(i)

    def _switch_folder(self, i: int):
        if not self.folders:
            return
        i = max(0, min(i, len(self.folders) - 1))

        if self._loaded_folder is not None:
            self.folder_state[self._loaded_folder] = {
                "seq": self.assigner.snapshot(),
                "rot_map": dict(self.rot_map),
                "idx": self.idx,
                "last_saved": self._last_saved,
            }

        self.folder_idx = i
        folder = self.folders[i]
        self._loaded_folder = folder
        self.pix = QPixmap()
        self.pix_orig = QPixmap()

        st = self.folder_state.get(folder)
        if st:
            self.assigner.restore(st["seq"])
            self.rot_map = dict(st.get("rot_map", {}))
            self._last_saved = st.get("last_saved", "")
            resume_idx = st.get("idx", 0)
        else:
            self.assigner = SeqAssigner()
            self.rot_map = {}
            self._last_saved = ""
            resume_idx = 0
        self.rot = 0

        self.files = [os.path.join(folder, n) for n in list_images(folder)]
        sizes = read_sizes(self.files)
        self._sizes = {k: s for k, s in enumerate(sizes)}

        self._refresh_folder_box()
        self._populate_list()
        if self.files:
            self._load(resume_idx if 0 <= resume_idx < len(self.files) else 0)
        else:
            self.view.set_pixmap(QPixmap())
            self.title_lbl.setText(f"{os.path.basename(folder)}（无图片）")
            self.info_lbl.setText(f"文件夹 {folder} 中没有图片")

    def _update_folder_buttons(self):
        self.btn_prev_folder.setEnabled(self.folder_idx > 0)
        self.btn_next_folder.setEnabled(self.folder_idx < len(self.folders) - 1)

    def prev_folder(self):
        if self.folder_idx > 0:
            self._switch_folder(self.folder_idx - 1)

    def next_folder(self):
        if self.folder_idx < len(self.folders) - 1:
            self._switch_folder(self.folder_idx + 1)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "添加要裁剪的文件夹")
        if not d:
            return
        d = os.path.normpath(d)
        if d in self.folders:
            self._switch_folder(self.folders.index(d))
            return
        self.folders.append(d)
        self._switch_folder(len(self.folders) - 1)
        self.folders_changed.emit(list(self.folders))

    def add_subdirs(self):
        d = QFileDialog.getExistingDirectory(self, "选择父目录（其下所有子目录都会加入列表）")
        if not d:
            return
        subs = [os.path.join(d, n) for n in sorted(os.listdir(d))
                if os.path.isdir(os.path.join(d, n))]
        if not subs:
            QMessageBox.information(self, "提示", "该目录下没有子目录。")
            return
        added = 0
        for s in subs:
            s = os.path.normpath(s)
            if s not in self.folders:
                self.folders.append(s)
                added += 1
        if added:
            self._switch_folder(len(self.folders) - 1)
            self.folders_changed.emit(list(self.folders))

    def remove_folder(self):
        if len(self.folders) <= 1:
            QMessageBox.information(self, "提示", "至少保留一个文件夹。")
            return
        cur = self.folders[self.folder_idx]
        r = QMessageBox.question(self, "确认",
                                 f"从列表中移除（不删除磁盘文件）：\n{cur}\n\n继续？")
        if r != QMessageBox.Yes:
            return
        self.folders.pop(self.folder_idx)
        self.folder_state.pop(cur, None)
        self._loaded_folder = None
        self._switch_folder(min(self.folder_idx, len(self.folders) - 1))
        self.folders_changed.emit(list(self.folders))

    # ================================================================ 图片列表

    def _digits(self) -> int:
        return max(3, len(str(max(len(self.files), 1))))

    def _threshold(self) -> tuple[int, int] | None:
        try:
            w = int(float(self.base_w_edit.text().strip()))
            h = int(float(self.base_h_edit.text().strip()))
        except Exception:  # noqa: BLE001
            return None
        return (w, h) if w > 0 and h > 0 else None

    def _size_state(self, i: int) -> str:
        th = self._threshold()
        sz = self._sizes.get(i)
        if not th or not sz:
            return ""
        return "over" if (sz[0] > th[0] or sz[1] > th[1]) else "fit"

    def _row_values(self, i: int) -> list[str]:
        d = self._digits()
        pos = f"{i+1:0{d}d}"
        out = ""
        if i in self.assigner.assign:
            seq = self.assigner.assign[i]
            n = self.assigner.block_counter.get(i, 0)
            sfx = "".join(suffix_at(k) for k in range(n))
            out = f"✔ {seq:0{d}d}{sfx}"
        sz = self._sizes.get(i)
        size_text = "—" if not sz else (
            ("⚠ " if self._size_state(i) == "over" else "") + f"{sz[0]}×{sz[1]}")
        rot = f"{self.rot_map[i]}°" if self.rot_map.get(i) else ""
        return [out, pos, os.path.basename(self.files[i]), size_text, rot]

    def _populate_list(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        mode = self.filter_box.currentText()
        shown = 0
        done_bg = QColor("#e9f8ef")
        done_fg = QColor("#12703c")
        over_bg = QColor("#fff3e0")
        over_fg = QColor("#a15300")
        for i in range(len(self.files)):
            if mode != "全部":
                st = self._size_state(i)
                if mode == "只看超出" and st != "over":
                    continue
                if mode == "只看可补白" and st != "fit":
                    continue
            it = QTreeWidgetItem(self._row_values(i))
            it.setData(0, Qt.UserRole, i)
            if i in self.assigner.assign:
                for c in range(5):
                    it.setBackground(c, done_bg)
                    it.setForeground(c, done_fg)
            elif self._size_state(i) == "over":
                for c in range(5):
                    it.setBackground(c, over_bg)
                    it.setForeground(c, over_fg)
            self.tree.addTopLevelItem(it)
            shown += 1
        self.tree.blockSignals(False)

        n_over = sum(1 for i in range(len(self.files)) if self._size_state(i) == "over")
        if mode == "全部":
            extra = f"  ·  ⚠ {n_over} 张超出" if n_over else ""
            self.count_lbl.setText(f"{len(self.files)} 张{extra}")
        else:
            self.count_lbl.setText(f"{shown}/{len(self.files)} 张")
        th = self._threshold()
        self.thr_lbl.setText(f"阈值 {th[0]}×{th[1]} · 超出 {n_over}" if th else "未设阈值")

    def _refresh_list_item(self, i: int):
        for row in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(row)
            if it.data(0, Qt.UserRole) == i:
                vals = self._row_values(i)
                for c, v in enumerate(vals):
                    it.setText(c, v)
                if i in self.assigner.assign:
                    for c in range(5):
                        it.setBackground(c, QColor("#e9f8ef"))
                        it.setForeground(c, QColor("#12703c"))
                break

    def _refresh_list_all(self):
        self._populate_list()

    def _on_tree_select(self, cur: QTreeWidgetItem | None, _prev):
        if cur is None:
            return
        i = cur.data(0, Qt.UserRole)
        if i is not None and 0 <= i < len(self.files) and i != self.idx:
            self._load(i)

    def goto_seq(self):
        s = "".join(ch for ch in self.goto_edit.text() if ch.isdigit())
        if not s:
            return
        n = int(s)
        if 1 <= n <= len(self.files):
            self._load(n - 1)
        else:
            QMessageBox.information(self, "提示",
                                    f"原图位次需在 1 ~ {len(self.files)} 之间。")

    def _scroll_to_current(self):
        for row in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(row)
            if it.data(0, Qt.UserRole) == self.idx:
                self.tree.blockSignals(True)
                self.tree.setCurrentItem(it)
                self.tree.blockSignals(False)
                self.tree.scrollToItem(it)
                break

    # ================================================================ 载入

    def _load(self, idx: int):
        if not self.files:
            return
        if idx >= len(self.files):
            self._finish()
            return
        idx = max(0, idx)
        self.idx = idx
        path = self.files[idx]
        pm = QPixmap(path)
        if pm.isNull():
            QMessageBox.critical(self, "打开失败", path)
            self.pix = QPixmap()
            self.pix_orig = QPixmap()
            return
        self.pix_orig = pm
        self.rot = self.rot_map.get(idx, 0) % 360
        self._rebuild_pix(keep_box=False)

        folder_name = os.path.basename(os.path.dirname(path)) if self.folders else ""
        self.title_lbl.setText(f"{folder_name}   ({idx+1}/{len(self.files)})   "
                               f"[册 {self.folder_idx+1}/{len(self.folders)}]")
        self._scroll_to_current()
        self._update_name_preview()
        self._settle_box()

    def _rebuild_pix(self, keep_box: bool = True):
        if self.pix_orig.isNull():
            return
        if self.rot % 360 == 0:
            self.pix = self.pix_orig
        else:
            from PySide6.QtGui import QTransform
            self.pix = self.pix_orig.transformed(
                QTransform().rotate(self.rot % 360), Qt.SmoothTransformation)
        first_show = not self.view.has_image() or \
            self.view.pix_item.pixmap().size() != self.pix.size()
        self.view.set_pixmap(self.pix)
        if first_show:
            self.view.fit_view()
        if not keep_box:
            self.box = [0, 0, self.pix.width(), self.pix.height()]
        else:
            self._set_box(*self.box)
        self.rot_lbl.setText(f"{self.rot}°")
        self._update_visuals()

    # ================================================================ 选框

    def _set_box(self, x, y, w, h):
        if self.pix.isNull():
            return
        W, H = self.pix.width(), self.pix.height()
        w = max(MIN_SIZE, min(int(w), W))
        h = max(MIN_SIZE, min(int(h), H))
        x = max(0, min(int(x), W - w))
        y = max(0, min(int(y), H - h))
        self.box = [x, y, w, h]

    def _box_rectf(self) -> QRectF:
        x, y, w, h = self.box
        return QRectF(x, y, w, h)

    def _read_size(self, silent=False) -> tuple[int, int] | None:
        try:
            w = int(float(self.w_edit.text().strip()))
            h = int(float(self.h_edit.text().strip()))
        except Exception:  # noqa: BLE001
            if not silent:
                QMessageBox.warning(self, "提示", "宽 / 高 必须是正整数。")
            return None
        if w <= 0 or h <= 0:
            if not silent:
                QMessageBox.warning(self, "提示", "宽 / 高 必须大于 0。")
            return None
        return w, h

    def apply_size(self):
        wh = self._read_size()
        if not wh or self.pix.isNull():
            return
        self.fixed_size = wh
        self._apply_fixed(silent=False)

    def _apply_fixed(self, silent: bool = False):
        if not self.fixed_size or self.pix.isNull():
            return
        w, h = self.fixed_size
        W, H = self.pix.width(), self.pix.height()
        self._set_box(self.box[0], self.box[1], w, h)
        self._place(self.align_mode)
        aw, ah = self.box[2], self.box[3]
        if not silent:
            self._sync_entry_from_box()
        if (aw, ah) != (w, h):
            self.size_hint.setProperty("warn", True)
            self.size_hint.setStyleSheet(f"color: {theme.WARN};")
            self.size_hint.setText(f"⚠ 原图仅 {W}×{H}，已收敛为 {aw}×{ah}。\n"
                                   f"要更大的成品尺寸请用「基底」。")
        else:
            tail = "  ·  换图自动套用中" if (silent and self.auto_apply_chk.isChecked()) else ""
            self.size_hint.setStyleSheet(f"color: {theme.OK};")
            self.size_hint.setText(f"✓ 已应用 {aw}×{ah}{tail}")
        self._update_visuals()

    def _settle_box(self):
        if self.auto_apply_chk.isChecked() and not self.pix.isNull():
            if self.fixed_size is None:
                self.fixed_size = self._read_size(silent=True)
            if self.fixed_size:
                self._apply_fixed(silent=True)
                return
        if self.fixed_size is None:
            self._sync_entry_from_box()
        self._update_visuals()

    def _on_auto_apply_toggle(self, on: bool):
        if on:
            if self.fixed_size is None:
                self.fixed_size = self._read_size(silent=True)
            if self.fixed_size:
                self._apply_fixed(silent=True)

    def quick(self, ratio_w, ratio_h):
        if self.pix.isNull():
            return
        w = round(self.pix.width() * ratio_w) if ratio_w else self.box[2]
        h = round(self.pix.height() * ratio_h) if ratio_h else self.box[3]
        self._set_box(self.box[0], self.box[1], w, h)
        self.fixed_size = None
        self._sync_entry_from_box()
        self._hint_applied()

    def _hint_applied(self):
        self.size_hint.setStyleSheet(f"color: {theme.OK};")
        self.size_hint.setText(f"✓ 已应用 {self.box[2]}×{self.box[3]}")
        self._update_visuals()

    def fit_whole(self):
        if self.pix.isNull():
            return
        self._set_box(0, 0, self.pix.width(), self.pix.height())
        self.fixed_size = None
        self._sync_entry_from_box()
        self._hint_applied()

    def _place(self, mode: str):
        if self.pix.isNull():
            return
        w = self.box[2]
        if mode == "left":
            x = 0
        elif mode == "right":
            x = max(0, self.pix.width() - w)
        else:
            x = max(0, (self.pix.width() - w) // 2)
        self._set_box(x, self.box[1], w, self.box[3])

    def align(self, mode: str):
        self.align_mode = mode
        self._place(mode)
        self._update_visuals()

    def _sync_entry_from_box(self):
        self.w_edit.setText(str(self.box[2]))
        self.h_edit.setText(str(self.box[3]))

    # ================================================================ 旋转

    def rotate(self, delta: int):
        if self.pix_orig.isNull():
            return
        self.rot = (self.rot + (180 if delta == 180 else delta)) % 360
        self.rot_map[self.idx] = self.rot
        self._rebuild_pix(keep_box=False)
        self._refresh_list_item(self.idx)
        self._settle_box()

    def rotate_reset(self):
        if self.pix_orig.isNull():
            return
        self.rot = 0
        self.rot_map[self.idx] = 0
        self._rebuild_pix(keep_box=False)
        self._refresh_list_item(self.idx)
        self._settle_box()

    def rotate_apply_rest(self):
        if self.pix_orig.isNull():
            return
        r = self.rot
        n = 0
        for i in range(len(self.files)):
            if i == self.idx:
                continue
            if i not in self.rot_map or self.rot_map[i] == 0:
                self.rot_map[i] = r
                n += 1
        self._refresh_list_all()
        QMessageBox.information(self, "完成", f"已把 {r}° 套用到本册其余 {n} 张图片。")

    # ================================================================ 基底 / 颜色

    def _color_mode(self) -> str:
        for rb in self.color_mode_group:
            if rb.isChecked():
                return rb.property("cval")
        return "white"

    def _current_base_rgb(self) -> tuple[int, int, int]:
        mode = self._color_mode()
        if mode == "custom":
            return tuple(self.custom_rgb)
        return PRESET_COLORS.get(mode, (255, 255, 255))

    def _left_rgb(self):
        return tuple(self.left_rgb) if self.sep_chk.isChecked() else self._current_base_rgb()

    def _right_rgb(self):
        return tuple(self.right_rgb) if self.sep_chk.isChecked() else self._current_base_rgb()

    def _base_config(self) -> BaseConfig:
        th = self._threshold()
        return BaseConfig(
            enabled=self.base_on_chk.isChecked(),
            bw=th[0] if th else 0,
            bh=th[1] if th else 0,
            left_rgb=self._left_rgb(),
            right_rgb=self._right_rgb(),
            pad_l=_parse_px(self.pad_l_edit.text()),
            pad_r=_parse_px(self.pad_r_edit.text()),
        )

    def _on_base_toggle(self, on: bool):
        if on and not self.pix.isNull():
            if not self.base_w_edit.text().strip():
                self.base_w_edit.setText(str(self.pix.width()))
            if not self.base_h_edit.text().strip():
                self.base_h_edit.setText(str(self.pix.height()))
        self._update_visuals()
        self._populate_list()

    def _on_base_size_changed(self):
        self._update_visuals()
        # 阈值输入去抖：每敲一个数字就重建几百行表格会卡（沿用旧版 250ms 去抖）
        if not hasattr(self, "_thr_timer"):
            self._thr_timer = QTimer(self)
            self._thr_timer.setSingleShot(True)
            self._thr_timer.timeout.connect(self._populate_list)
        self._thr_timer.start(250)

    def base_fill_current(self):
        if self.pix.isNull():
            return
        self.base_w_edit.setText(str(self.pix.width()))
        self.base_h_edit.setText(str(self.pix.height()))

    def _update_swatch(self):
        rgb = self._current_base_rgb()
        self.swatch.setStyleSheet(f"border:1px solid #d8dee9; background:{_hex(rgb)};")

    def _update_side_swatches(self):
        self.swatch_l.setStyleSheet(
            f"border:1px solid #d8dee9; background:{_hex(self.left_rgb)};")
        self.swatch_r.setStyleSheet(
            f"border:1px solid #d8dee9; background:{_hex(self.right_rgb)};")

    def _on_color_change(self):
        self._update_swatch()
        self._update_side_swatches()
        self._update_visuals()

    def set_base_color(self, rgb, mode: str = "custom"):
        self.custom_rgb = tuple(int(v) for v in rgb[:3])
        for rb in self.color_mode_group:
            rb.blockSignals(True)
            rb.setChecked(rb.property("cval") == mode)
            rb.blockSignals(False)
        self._on_color_change()

    def set_side_color(self, side: str, rgb):
        rgb = tuple(int(v) for v in rgb[:3])
        if side == "left":
            self.left_rgb = rgb
        else:
            self.right_rgb = rgb
        self.sep_chk.setChecked(True)
        self._on_color_change()

    def pick_color_dialog(self):
        cur = self._current_base_rgb()
        c = QColorDialog.getColor(QColor(*cur), self, "选择基底底色")
        if c.isValid():
            self.set_base_color((c.red(), c.green(), c.blue()), "custom")

    def pick_side_color(self, side: str):
        cur = self.left_rgb if side == "left" else self.right_rgb
        c = QColorDialog.getColor(QColor(*cur), self,
                                  "选择左补颜色" if side == "left" else "选择右补颜色")
        if c.isValid():
            self.set_side_color(side, (c.red(), c.green(), c.blue()))

    def _sync_base_w_from_pads(self):
        l, r = _parse_px(self.pad_l_edit.text()), _parse_px(self.pad_r_edit.text())
        if not self.pix.isNull() and (l or r):
            self.base_w_edit.blockSignals(True)
            self.base_w_edit.setText(str(self.box[2] + l + r))
            self.base_w_edit.blockSignals(False)
            self._populate_list()
        self._update_visuals()

    # ================================================================ 取色

    def start_eyedrop(self, side: str = "base"):
        if self.pix.isNull():
            return
        if self._picking and self._eyedrop_side == side:
            self._exit_eyedrop()
            return
        self._eyedrop_side = side
        self._picking = True
        self.view.setCursor(Qt.CrossCursor)
        what = {"base": "基底底色", "left": "左补颜色", "right": "右补颜色"}[side]
        self.info_lbl.setText(f"取色模式：请在图上点击要作为【{what}】的位置（Esc 取消）")

    def _exit_eyedrop(self):
        self._picking = False
        self.view.unsetCursor()

    def _sample_at(self, scene_pos: QPointF) -> bool:
        if self.pix.isNull():
            return False
        img = self.pix.toImage()
        ix = int(max(0, min(scene_pos.x(), img.width() - 1)))
        iy = int(max(0, min(scene_pos.y(), img.height() - 1)))
        c = img.pixelColor(ix, iy)
        px = (c.red(), c.green(), c.blue())
        if self._eyedrop_side == "left":
            self.set_side_color("left", px)
        elif self._eyedrop_side == "right":
            self.set_side_color("right", px)
        else:
            self.set_base_color(px, "custom")
        self.info_lbl.setText(f"已取色：{_hex(px)}  (图上 x={ix} y={iy})")
        return True

    # ================================================================ 画布交互

    def _fit_view(self):
        self.view.fit_view()

    def _on_press(self, pos: QPointF):
        if self.pix.isNull():
            return
        if self._picking:
            self._sample_at(pos)
            self._exit_eyedrop()
            return

        r = self._box_rectf()
        hd = self.view.hit_handle(pos, r) if self.show_overlay else None
        self._drag_start = pos
        self._drag_box0 = tuple(self.box)
        self._pending_outside = None

        inside = r.contains(pos)
        if not self.show_overlay:
            if inside:
                self._set_overlay(True)
            return

        if hd:
            self._drag_mode = hd
        elif inside:
            self._drag_mode = "move"
        else:
            self._drag_mode = None
            self._pending_outside = pos

    def _on_drag(self, pos: QPointF):
        if self.pix.isNull():
            return
        # 框外按下后拖动 → 以按下点为中心搬选框
        if self._pending_outside is not None and self._drag_mode is None:
            if abs(pos.x() - self._pending_outside.x()) + \
               abs(pos.y() - self._pending_outside.y()) < 5 / max(self.view.view_scale(), 1e-6):
                return
            w, h = self.box[2], self.box[3]
            self._set_box(self._pending_outside.x() - w / 2,
                          self._pending_outside.y() - h / 2, w, h)
            self._drag_mode = "move"
            self._drag_start = self._pending_outside
            self._drag_box0 = tuple(self.box)
            self._pending_outside = None

        if not self._drag_mode:
            return
        dx = pos.x() - self._drag_start.x()
        dy = pos.y() - self._drag_start.y()
        x0, y0, w0, h0 = self._drag_box0
        m = self._drag_mode

        if m == "move":
            self._set_box(x0 + dx, y0 + dy, w0, h0)
        else:
            nx0, ny0, nx1, ny1 = x0, y0, x0 + w0, y0 + h0
            if "n" in m:
                ny0 = y0 + dy
            if "s" in m:
                ny1 = y0 + h0 + dy
            if "w" in m:
                nx0 = x0 + dx
            if "e" in m:
                nx1 = x0 + w0 + dx
            if nx1 < nx0:
                nx0, nx1 = nx1, nx0
            if ny1 < ny0:
                ny0, ny1 = ny1, ny0
            self._set_box(nx0, ny0, nx1 - nx0, ny1 - ny0)
        self._sync_entry_from_box()
        self._update_visuals()

    def _on_release(self, _pos: QPointF):
        if self._pending_outside is not None:
            # 框外只是点了一下（没拖动）→ 隐藏选框
            self._pending_outside = None
            self._drag_mode = None
            self._set_overlay(False)
            return
        self._drag_mode = None
        if _parse_px(self.pad_l_edit.text()) or _parse_px(self.pad_r_edit.text()):
            self._sync_base_w_from_pads()

    def toggle_overlay(self):
        self._set_overlay(not self.show_overlay)

    def _set_overlay(self, on: bool):
        self.show_overlay = on
        self.overlay_chk.blockSignals(True)
        self.overlay_chk.setChecked(on)
        self.overlay_chk.blockSignals(False)
        self._update_visuals()

    def _on_overlay_chk(self, on: bool):
        self.show_overlay = on
        self._update_visuals()

    def _on_escape(self):
        if self._picking:
            self._exit_eyedrop()
            return
        if not self.show_overlay:
            self._set_overlay(True)

    # ================================================================ 画布刷新

    def _update_visuals(self):
        if self.pix.isNull():
            return
        r = self._box_rectf()
        self.view.set_crop_rect(r, self.show_overlay)
        self.view.set_dim(r)

        x, y, w, h = self.box
        base = self._base_config()
        if base.enabled and base.valid():
            l, rp = base.pad_l, base.pad_r
            ox = l if (l or rp) else max(0, (base.bw - w) // 2)
            oy = max(0, (base.bh - h) // 2)
            if ox + w > base.bw:
                ox = max(0, base.bw - w)
            br = QRectF(x - ox, y - oy, base.bw, base.bh)
            fits = (w <= base.bw and h <= base.bh)
            edge = QColor(theme.WARN if not fits else "#7c3aed")
            self.view.set_base_preview(br, QColor(_hex(base.left_rgb)),
                                       QColor(_hex(base.right_rgb or base.left_rgb)),
                                       edge)
            if not fits:
                self.canvas_hint.setText(f"⚠ 基底 {base.bw}×{base.bh} 比内容小 → 这一张不加基底")
            else:
                pad_x = max(0, (base.bw - w) // 2)
                pad_y = max(0, (base.bh - h) // 2)
                if l or rp:
                    txt = f"基底 {base.bw}×{base.bh}（紫框） · 左补 {l}px · 右补 {rp}px"
                elif pad_x or pad_y:
                    txt = f"基底 {base.bw}×{base.bh}（紫框） · 左右补 {pad_x}px · 上下补 {pad_y}px"
                else:
                    txt = f"基底 {base.bw}×{base.bh}（与内容同尺寸）"
                if base.right_rgb and base.right_rgb != base.left_rgb:
                    txt += " · 左右分色"
                self.canvas_hint.setText(txt)
        else:
            self.view.set_base_preview(None, QColor(), QColor(), QColor())
            self.canvas_hint.setText("滚轮缩放 · 右键还原视图")

        if base.enabled and base.valid():
            out_size = f"{base.bw}×{base.bh} 底色{_hex(base.left_rgb)}"
        elif base.enabled:
            out_size = "基底尺寸未填"
        else:
            out_size = f"{w}×{h}"
        scale_pct = int(self.view.view_scale() * 100)
        self.info_lbl.setText(
            f"原图 {self.pix.width()}×{self.pix.height()}   旋转 {self.rot}°   "
            f"选框 {w}×{h} @ ({x},{y})   输出 {out_size}   "
            f"缩放 {scale_pct}%   已存 {self.saved_count} 张"
            + (f"   最近 {os.path.basename(self._last_saved)}" if self._last_saved else "")
            + f"\n→ {self._out_dir_for(self.idx)}")

    # ================================================================ 输出路径 / 命名

    def _default_out_dir(self) -> str:
        if self.out_name_fn and 0 <= self.folder_idx < len(self.folders):
            folder = self.folders[self.folder_idx]
            try:
                name = self.out_name_fn(folder)
            except Exception:  # noqa: BLE001
                name = os.path.basename(folder)
            return os.path.join(os.path.dirname(folder), name)
        return os.path.join(os.path.expanduser("~"), "crop_out")

    def _out_dir_for(self, file_index: int) -> str:
        d = self.out_edit.text().strip()
        if d:
            return d
        if self.follow_src_chk.isChecked() and 0 <= file_index < len(self.files):
            return os.path.dirname(os.path.abspath(self.files[file_index]))
        return self._default_out_dir()

    def _render_name(self, file_index: int, seq: int, sfx: str) -> str:
        tpl = self.name_tpl_edit.text().strip()
        folder = self.folders[self.folder_idx] if self.folders else ""
        src = (os.path.basename(self.files[file_index])
               if file_index < len(self.files) else "img")
        return render_output_filename(tpl, seq, sfx, src, folder)

    def _update_name_preview(self):
        if not self.files or self.idx >= len(self.files):
            self.name_preview.setText("")
            return
        i = self.idx
        seq = self.assigner.assign.get(i, self.assigner.next_seq)
        block = self.assigner.peek_suffix(i)
        try:
            nm = self._render_name(i, seq, block)
        except Exception:  # noqa: BLE001
            nm = "（模板无效）"
        self.name_preview.setText(f"下一张将存为：{nm}")

    def _out_path_for(self, file_index: int) -> str:
        seq = self.assigner.seq_num(file_index)
        sfx = self.assigner.next_suffix(file_index)
        name = self._render_name(file_index, seq, sfx)
        d = self._out_dir_for(file_index)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        key = os.path.normcase(path)
        k = 2
        while key in self._used_paths:
            stem, ext = os.path.splitext(name)
            path = os.path.join(d, f"{stem}_{k}{ext}")
            key = os.path.normcase(path)
            k += 1
        self._used_paths.add(key)
        return path

    def choose_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出文件夹",
                                             self._out_dir_for(self.idx))
        if d:
            self.out_edit.setText(os.path.normpath(d))
            self._update_visuals()

    def open_out_dir(self):
        d = (self._out_dir_for(self.idx) if self.follow_src_chk.isChecked()
             else (self.out_edit.text().strip() or self._default_out_dir()))
        if not os.path.isdir(d):
            QMessageBox.information(self, "提示", "输出文件夹尚不存在。")
            return
        os.startfile(os.path.normpath(d))  # type: ignore[attr-defined]

    def _out_dir_desc(self) -> str:
        d = self.out_edit.text().strip()
        if d:
            return f"{d}（自定义目录）"
        if self.follow_src_chk.isChecked():
            return "各自的「原图所在文件夹」（每张图跟着它自己的目录）"
        return self._default_out_dir()

    # ================================================================ 保存

    def _save_current(self) -> bool:
        if self.pix.isNull():
            return False
        x, y, w, h = self.box
        out = self._out_path_for(self.idx)
        base = self._base_config()
        try:
            _w, _h, fits = render_crop(self.files[self.idx],
                                       CropSpec(x, y, w, h), self.rot, base, out)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", f"{out}\n{e}")
            return False
        if base.enabled and base.valid() and not fits and not self._base_warned:
            self._base_warned = True
            QMessageBox.warning(self, "基底太小",
                                f"裁出的图 {w}x{h} 比基底 {base.bw}x{base.bh} 大，无法放入。\n"
                                f"为避免裁掉内容，这一张已按原尺寸保存（不加基底）。")
        self.saved_count += 1
        self._last_saved = out
        self._refresh_list_item(self.idx)
        self._update_name_preview()
        self._update_visuals()
        return True

    def save_only(self):
        self._save_current()

    def save_next(self):
        if self.apply_rest_chk.isChecked():
            self.batch_apply(from_current=True)
            return
        if self._save_current():
            self._load(self.idx + 1)

    def batch_apply(self, from_current: bool = True):
        """
        以当前选框宽高为准，对「当前及其后」所有图片批量裁剪（后台线程执行）。
        """
        if self.pix.isNull() or self._worker is not None:
            return
        w, h = self.box[2], self.box[3]
        todo = list(enumerate(self.files))[self.idx + 1:]
        if not todo and not from_current:
            return
        base = self._base_config()
        base_desc = (f"启用 {base.bw}x{base.bh}"
                     if base.enabled and base.valid() else "未启用")
        r = QMessageBox.question(
            self, "确认",
            f"将对{'当前这张 + 后面 ' if from_current else '后面 '}{len(todo)} 张图批量裁剪，"
            f"尺寸 {w} x {h}（居中放置）。\n"
            f"旋转：未单独设过角度的图套用当前 {self.rot}°；已设过的保持自己的角度。\n"
            f"基底：{base_desc}\n\n输出位置：{self._out_dir_desc()}\n\n继续？")
        if r != QMessageBox.Yes:
            return
        if from_current:
            self._save_current()
        if not todo:
            self._finish()
            return

        self.save_settings()
        rot_map = dict(self.rot_map)
        cur_rot = self.rot
        out_paths: dict[int, str] = {}
        # 预先分配输出路径（序号/后缀分配必须在主线程顺序执行，避免竞态）
        for fi, _p in todo:
            out_paths[fi] = self._out_path_for(fi)

        dlg = QProgressDialog("正在批量裁剪…", "取消", 0, len(todo), self)
        dlg.setWindowTitle("批量裁剪")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)

        self._worker = FuncWorker(
            lambda progress=None, cancel=None, log=None: batch_crop(
                todo, (w, h),
                lambda fi: rot_map.get(fi, cur_rot),
                lambda fi: out_paths[fi],
                base, progress=progress, cancel=cancel),
            parent=self)

        def on_prog(done, total, name):
            dlg.setMaximum(total)
            dlg.setValue(done)
            dlg.setLabelText(f"正在裁剪 {done}/{total}：{name}")
            if dlg.wasCanceled():
                self._worker.stop()

        def on_finish(res: BatchResult):
            dlg.close()
            self._worker = None
            self.saved_count += res.done
            if res.outputs:
                self._last_saved = res.outputs[-1]
            self._refresh_list_all()
            msg = f"批量裁剪完成：成功 {res.done} 张"
            if res.skipped_base:
                msg += f"\n内容大于基底、按原尺寸保存：{res.skipped_base} 张"
            if res.failed:
                msg += f"\n失败 {res.failed} 张：\n" + "\n".join(res.errors[:8])
            if res.cancelled:
                msg += "\n（已取消，未完成全部）"
            QMessageBox.information(self, "批量裁剪", msg)
            self._update_visuals()
            self._finish()

        def on_fail(tb):
            dlg.close()
            self._worker = None
            QMessageBox.critical(self, "批量裁剪异常", tb)

        self._worker.progress.connect(on_prog)
        self._worker.finished_ok.connect(on_finish)
        self._worker.failed.connect(on_fail)
        dlg.canceled.connect(self._worker.stop)
        self._worker.start()
        dlg.show()

    def batch_add_base(self):
        """把某个文件夹里已裁好的成品统一居中补到基底尺寸（后台线程执行）。"""
        th = self._threshold()
        if not th:
            QMessageBox.warning(self, "提示", "请先填写有效的基底宽 / 高。")
            return
        base = self._base_config()
        guess = self._default_out_dir()
        if not os.path.isdir(guess):
            guess = (self.folders[self.folder_idx]
                     if 0 <= self.folder_idx < len(self.folders)
                     else os.path.expanduser("~"))
        src = QFileDialog.getExistingDirectory(self, "选择要补白的成品文件夹", guess)
        if not src:
            return
        src = os.path.normpath(src)
        out = src + "_base"
        names = list_images(src)
        if not names:
            QMessageBox.information(self, "提示", "该文件夹里没有图片。")
            return
        pos = (f"左边距固定 {base.pad_l}px（右补 {base.pad_r}px）"
               if (base.pad_l or base.pad_r) else "内容居中放置")
        col = (f"左补 {_hex(base.left_rgb)} / 右补 {_hex(base.right_rgb)}"
               if base.right_rgb and base.right_rgb != base.left_rgb
               else f"底色 {_hex(base.left_rgb)}")
        r = QMessageBox.question(
            self, "批量给成品补白",
            f"源文件夹：{src}\n共 {len(names)} 张\n\n"
            f"补到尺寸：{base.bw} × {base.bh}\n颜色：{col}\n"
            f"位置：{pos}\n输出到：{out}\n\n"
            f"比基底还大的图会跳过（不裁内容）。\n"
            f"若输出目录已存在同名文件会被覆盖。\n\n继续？")
        if r != QMessageBox.Yes:
            return

        dlg = QProgressDialog(f"共 {len(names)} 张，正在补白…", "取消",
                              0, len(names), self)
        dlg.setWindowTitle("批量补白")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)

        self._worker = FuncWorker(
            lambda progress=None, cancel=None, log=None: batch_add_base(
                src, out, base, progress=progress, cancel=cancel),
            parent=self)

        def on_prog(done, total, _n):
            dlg.setValue(done)
            if dlg.wasCanceled():
                self._worker.stop()

        def on_finish(res: BatchResult):
            dlg.close()
            self._worker = None
            msg = (f"完成：{res.done} 张\n跳过（比基底大）：{res.skipped_base} 张\n"
                   f"失败：{res.failed} 张\n\n输出：{out}")
            if res.errors:
                msg += "\n\n失败明细（前几条）：\n" + "\n".join(res.errors[:8])
            QMessageBox.information(self, "批量补白完成", msg)
            self.info_lbl.setText(f"批量补白完成：{res.done} 张 → {out}")

        def on_fail(tb):
            dlg.close()
            self._worker = None
            QMessageBox.critical(self, "批量补白异常", tb)

        self._worker.progress.connect(on_prog)
        self._worker.finished_ok.connect(on_finish)
        self._worker.failed.connect(on_fail)
        dlg.canceled.connect(self._worker.stop)
        self._worker.start()
        dlg.show()

    # ================================================================ 导航

    def prev(self):
        if self.idx > 0:
            self._load(self.idx - 1)

    def next(self):
        self._load(self.idx + 1)

    def _finish(self):
        loc = (self.out_edit.text().strip()
               or ("各原图所在文件夹" if self.follow_src_chk.isChecked()
                   else self._default_out_dir()))
        if self.folder_idx < len(self.folders) - 1:
            nxt = os.path.basename(self.folders[self.folder_idx + 1])
            r = QMessageBox.question(
                self, "本册走完",
                f"本册已到最后一张。累计保存 {self.saved_count} 张。\n"
                f"输出位置：{loc}\n\n是否切换到下一册？\n{nxt}")
            if r == QMessageBox.Yes:
                self._switch_folder(self.folder_idx + 1)
            return
        QMessageBox.information(self, "本册完成",
                                f"已保存 {self.saved_count} 张。\n输出位置：{loc}")
