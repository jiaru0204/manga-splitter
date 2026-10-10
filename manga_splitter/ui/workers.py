# -*- coding: utf-8 -*-
"""
后台任务封装：把普通函数扔进 QThread，用信号回报进度/结果/异常。

替代旧版的「threading.Thread + queue.Queue + after 轮询」三件套：
Qt 信号天然跨线程安全，UI 代码只需 connect，不需要手动泵消息。
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class FuncWorker(QThread):
    """
    在子线程跑 fn(*args, **kwargs)。
      fn 可通过约定参数拿到回调：
        progress(done, total, name)  -> 信号 progress
        cancel()  -> bool            -> 外部调用 stop() 后为 True
    用法：
        w = FuncWorker(fn, with_callbacks=True)
        w.progress.connect(...); w.finished.connect(...); w.failed.connect(...)
        w.kwargs["progress"] = w.emit_progress 等已在内部注入
    """

    progress = Signal(int, int, str)
    log = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, parent=None, **kwargs):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._stop = False

    def stop(self):
        self._stop = True

    def is_stop_requested(self) -> bool:
        return self._stop

    def emit_progress(self, done: int, total: int, name: str = ""):
        self.progress.emit(done, total, name or "")

    def emit_log(self, text: str):
        self.log.emit(text)

    def run(self):
        import traceback
        self._kwargs.setdefault("progress", self.emit_progress)
        self._kwargs.setdefault("cancel", self.is_stop_requested)
        self._kwargs.setdefault("log", self.emit_log)
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.finished_ok.emit(result)
        except Exception:  # noqa: BLE001
            self.failed.emit(traceback.format_exc())
