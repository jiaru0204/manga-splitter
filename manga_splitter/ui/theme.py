# -*- coding: utf-8 -*-
"""
浅色主题（QSS）。配色沿用旧版 ui_theme.py 的基调，观感保持一致但更干净。
"""

BG      = "#eef1f6"
CARD    = "#ffffff"
BORDER  = "#d8dee9"
TEXT    = "#1e2530"
MUTED   = "#7b8798"
ACCENT  = "#2f6feb"
ACCENT2 = "#1f57c4"
OK      = "#18a058"
WARN    = "#d97706"
STAGE   = "#c9d0da"

FONT_FAMILY = "Microsoft YaHei UI"

QSS = f"""
* {{
    font-family: "{FONT_FAMILY}";
    font-size: 13px;
    color: {TEXT};
    outline: none;
}}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#root {{ background: {BG}; }}

/* ---------- 侧栏导航 ---------- */
QListWidget#nav {{
    background: #e4e9f2;
    border: none;
    padding: 8px 6px;
    font-size: 14px;
}}
QListWidget#nav::item {{
    padding: 10px 14px;
    border-radius: 8px;
    margin: 2px 4px;
    color: #48546a;
}}
QListWidget#nav::item:hover {{ background: #d9e1ee; }}
QListWidget#nav::item:selected {{
    background: {ACCENT};
    color: #ffffff;
    font-weight: bold;
}}

/* ---------- 卡片 ---------- */
QGroupBox.card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 14px;
    padding: 10px 12px 12px 12px;
    font-weight: bold;
    color: {ACCENT};
}}
QGroupBox.card::title {{
    subcontrol-origin: margin;
    left: 12px;
    top: 2px;
    padding: 0 4px;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: #f7f9fc;
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background: #eaf0fa; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #e2e8f2; }}
QPushButton:disabled {{ color: #a9b2c0; background: #f2f4f8; border-color: #e5e9f0; }}
QPushButton[accent="true"] {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: #ffffff;
    font-weight: bold;
}}
QPushButton[accent="true"]:hover {{ background: {ACCENT2}; }}
QPushButton[accent="true"]:disabled {{ background: #a8c0ee; border-color: #a8c0ee; }}
QPushButton[ok="true"] {{
    background: {OK};
    border-color: {OK};
    color: #ffffff;
    font-weight: bold;
}}
QPushButton[ok="true"]:hover {{ background: #159150; }}

/* ---------- 输入 ---------- */
QLineEdit, QComboBox, QSpinBox {{
    background: #ffffff;
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: #ffffff;
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}
QCheckBox, QRadioButton {{ spacing: 6px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}

/* ---------- 表格 / 列表 ---------- */
QTreeWidget, QTableWidget, QListWidget.list {{
    background: #ffffff;
    border: 1px solid {BORDER};
    border-radius: 8px;
    alternate-background-color: #f6f8fc;
    gridline-color: transparent;
}}
QTreeWidget::item, QListWidget.list::item {{ padding: 3px 4px; }}
QTreeWidget::item:selected, QListWidget.list::item:selected {{
    background: {ACCENT};
    color: #ffffff;
}}
QHeaderView::section {{
    background: #e7ecf4;
    color: #48546a;
    border: none;
    padding: 6px 4px;
    font-weight: bold;
    font-size: 12px;
}}

/* ---------- 进度条 ---------- */
QProgressBar {{
    background: #dde3ec;
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

/* ---------- 日志 ---------- */
QPlainTextEdit#log {{
    background: #fbfcfe;
    border: 1px solid {BORDER};
    border-radius: 8px;
    font-family: Consolas, "Microsoft YaHei UI";
    font-size: 12px;
    color: #3a4557;
}}

/* ---------- 标签变体 ---------- */
QLabel[muted="true"] {{ color: {MUTED}; font-size: 12px; }}
QLabel[accent="true"] {{ color: {ACCENT}; }}
QLabel[ok="true"] {{ color: {OK}; }}
QLabel[warn="true"] {{ color: {WARN}; }}
QLabel[h1="true"] {{ font-size: 17px; font-weight: bold; }}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #c3ccd9; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #a9b4c6; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #c3ccd9; border-radius: 5px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: #a9b4c6; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---------- 画布 ---------- */
QGraphicsView#stage {{
    background: {STAGE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

QToolTip {{
    background: #2b3444;
    color: #ffffff;
    border: none;
    padding: 5px 8px;
}}
QScrollArea {{ border: none; background: transparent; }}
QSplitter::handle {{ background: {BORDER}; width: 1px; }}
"""
