# -*- coding: utf-8 -*-
"""
设置持久化：JSON 文件。

便携优先：配置存放在 exe（或 main.py）同目录的 settings.json ——
整个文件夹拷到别的电脑，配置跟着走。目录不可写时（如 Program Files）
回退到 %APPDATA%/manga-splitter/settings.json。

旧版完全没有配置持久化，每次启动都要重新添加文件夹、重填模板，
这是重构要解决的痛点之一。
"""

from __future__ import annotations

import json
import os
import sys


def app_dir() -> str:
    """程序所在目录（onefile exe 取 exe 所在目录，而非解压临时目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def settings_path() -> str:
    d = app_dir()
    p = os.path.join(d, "settings.json")
    try:
        os.makedirs(d, exist_ok=True)
        if os.path.exists(p):
            with open(p, "a", encoding="utf-8"):
                pass
        else:
            with open(p, "w", encoding="utf-8") as f:
                f.write("{}")
            os.remove(p)
        return p
    except OSError:
        fb = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                          "manga-splitter")
        os.makedirs(fb, exist_ok=True)
        return os.path.join(fb, "settings.json")


class Settings:
    """极简 KV 配置：读写在内存字典上，save() 落盘。"""

    def __init__(self):
        self.path = settings_path()
        self.data: dict = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            if not isinstance(self.data, dict):
                self.data = {}
        except Exception:  # noqa: BLE001
            self.data = {}

    def save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001
            pass

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value
