# -*- coding: utf-8 -*-
"""
模式二：按指定分辨率裁剪（框选截取）的纯逻辑层。

从旧 crop_panel.py（2200 行 UI 巨类）中剥离出的可测试部分：
  - CropSpec          裁剪框（含边界收敛）
  - BaseConfig        基底（统一输出分辨率）配置
  - SeqAssigner       输出序号 / 块后缀分配（纯状态机，与文件夹进度一起保存）
  - render_crop       单张图：旋转 -> 裁剪 -> 基底合成 -> 保存
  - batch_crop        批量裁剪（进度 / 取消 / 错误上报，不再静默吞错）
  - batch_add_base    给已裁成品批量补白（同上）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image

from .imaging import compose_base, list_images, normalize, rotated
from .naming import render_output_filename, suffix_at

Progress = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


@dataclass
class CropSpec:
    """一次裁剪规格：在源图中截取 (x, y, w, h) 这一块。"""
    x: int
    y: int
    w: int
    h: int

    def clamp_to(self, img_w: int, img_h: int) -> "CropSpec":
        w = min(self.w, img_w)
        h = min(self.h, img_h)
        x = max(0, min(self.x, img_w - w))
        y = max(0, min(self.y, img_h - h))
        return CropSpec(x, y, w, h)


@dataclass
class BaseConfig:
    """
    基底配置：把裁出的内容统一贴到 bw×bh 画布上。
    enabled=False 时原样输出。
    pad_l/pad_r 任一非零时水平位置 = 左补（否则居中），垂直始终居中。
    """
    enabled: bool = False
    bw: int = 0
    bh: int = 0
    left_rgb: tuple[int, int, int] = (255, 255, 255)
    right_rgb: tuple[int, int, int] | None = None   # None = 与 left 同色
    pad_l: int = 0
    pad_r: int = 0

    def valid(self) -> bool:
        return self.enabled and self.bw > 0 and self.bh > 0

    def apply(self, piece: Image.Image) -> tuple[Image.Image, bool]:
        """
        合成输出。返回 (图, 是否放下了)。
        内容比基底大时不裁内容 —— 原样返回并标记 False（由调用方决定是否警告）。
        """
        if not self.valid():
            return piece, True
        if piece.width > self.bw or piece.height > self.bh:
            return piece, False
        return compose_base(piece, self.bw, self.bh,
                            self.left_rgb, self.right_rgb,
                            self.pad_l, self.pad_r), True


@dataclass
class SeqAssigner:
    """
    输出序号分配（对应旧 crop_panel 的 assign / next_seq / block_counter）：
      - 序号按「裁剪顺序」从 1 递增，与图片在文件夹中的位次无关；
      - 同一张图反复裁剪，复用首次分配的序号，块后缀 a/b/c 递增。
    """
    assign: dict[int, int] = field(default_factory=dict)
    block_counter: dict[int, int] = field(default_factory=dict)
    next_seq: int = 1

    def seq_num(self, file_index: int) -> int:
        if file_index not in self.assign:
            self.assign[file_index] = self.next_seq
            self.next_seq += 1
        return self.assign[file_index]

    def next_suffix(self, file_index: int) -> str:
        n = self.block_counter.get(file_index, 0)
        self.block_counter[file_index] = n + 1
        return suffix_at(n)

    def peek_suffix(self, file_index: int) -> str:
        return suffix_at(self.block_counter.get(file_index, 0))

    def snapshot(self) -> dict:
        return {"assign": dict(self.assign),
                "block_counter": dict(self.block_counter),
                "next_seq": self.next_seq}

    def restore(self, st: dict):
        self.assign = dict(st.get("assign", {}))
        self.block_counter = dict(st.get("block_counter", {}))
        self.next_seq = st.get("next_seq", 1)


def render_crop(src_path: str, spec: CropSpec, rot_cw: int,
                base: BaseConfig, out_path: str) -> tuple[int, int, bool]:
    """
    单张图完整流水线：打开 -> 旋转 -> 裁剪 -> 基底合成 -> 保存 PNG。
    返回 (输出宽, 输出高, 是否成功放入基底)。
    """
    with Image.open(src_path) as im:
        work = rotated(normalize(im), rot_cw)
        spec = spec.clamp_to(work.width, work.height)
        piece = work.crop((spec.x, spec.y, spec.x + spec.w, spec.y + spec.h))
    out_img, fits = base.apply(piece)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    out_img.save(out_path, "PNG")
    return out_img.width, out_img.height, fits


@dataclass
class BatchResult:
    done: int = 0
    failed: int = 0
    skipped_base: int = 0          # 内容比基底大、按原尺寸保存的张数
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    outputs: list[str] = field(default_factory=list)


def batch_crop(todo: list[tuple[int, str]], spec_size: tuple[int, int],
               rot_of: Callable[[int], int],
               out_path_of: Callable[[int], str],
               base: BaseConfig,
               progress: Progress | None = None,
               cancel: CancelCheck | None = None) -> BatchResult:
    """
    批量居中裁剪：对 todo 里每张 (原图索引, 路径)，
    按该图角度旋转后居中裁 spec_size（原图不足时自动内缩），再套用基底。

    与旧版 crop_panel.batch_apply 的差异：
      - 不再 `except: pass` 静默吞错 —— 失败逐条记入 result.errors；
      - 支持 cancel 即时中断；
      - 可跑在后台线程（不碰任何 GUI 对象）。
    """
    res = BatchResult()
    w, h = spec_size
    total = len(todo)
    _made_dirs: set[str] = set()
    for k, (fi, p) in enumerate(todo, 1):
        if cancel and cancel():
            res.cancelled = True
            break
        try:
            with Image.open(p) as im:
                work = rotated(normalize(im), rot_of(fi) % 360)
                cw, ch = min(w, work.width), min(h, work.height)
                cx = max(0, (work.width - cw) // 2)
                cy = max(0, (work.height - ch) // 2)
                piece = work.crop((cx, cy, cx + cw, cy + ch))
            out_img, fits = base.apply(piece)
            if not fits:
                res.skipped_base += 1
            out = out_path_of(fi)
            d = os.path.dirname(os.path.abspath(out))
            if d not in _made_dirs:      # 每张图都 makedirs 是浪费，按目录去重
                os.makedirs(d, exist_ok=True)
                _made_dirs.add(d)
            out_img.save(out, "PNG")
            res.done += 1
            res.outputs.append(out)
        except Exception as e:  # noqa: BLE001
            res.failed += 1
            res.errors.append(f"{os.path.basename(p)}: {e}")
        if progress:
            progress(k, total, os.path.basename(p))
    return res


def batch_add_base(src: str, out: str, base: BaseConfig,
                   progress: Progress | None = None,
                   cancel: CancelCheck | None = None) -> BatchResult:
    """
    把 src 文件夹里已裁好的成品统一补到基底尺寸（不重新裁剪）。
    比基底还大的图跳过（不裁内容），文件名保持原名（扩展名统一 .png）。
    """
    res = BatchResult()
    names = list_images(src)
    total = len(names)
    try:
        os.makedirs(out, exist_ok=True)
    except OSError as e:
        res.errors.append(str(e))
        return res

    for i, n in enumerate(names, 1):
        if cancel and cancel():
            res.cancelled = True
            break
        p = os.path.join(src, n)
        try:
            with Image.open(p) as im:
                im = normalize(im)
                W, H = im.size
                if W <= base.bw and H <= base.bh:
                    canvas = compose_base(im, base.bw, base.bh,
                                          base.left_rgb, base.right_rgb,
                                          base.pad_l, base.pad_r)
                    canvas.save(os.path.join(
                        out, os.path.splitext(n)[0] + ".png"), "PNG")
                    res.done += 1
                else:
                    res.skipped_base += 1
        except Exception as e:  # noqa: BLE001
            res.failed += 1
            res.errors.append(f"{n}: {e}")
        if progress:
            progress(i, total, n)
    return res
