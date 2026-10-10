# -*- coding: utf-8 -*-
"""
模式一：漫画双页自动分割。

规则（与旧版 splitter_core 完全一致）：
  1. 命名：纯数字 001, 002, 003 ... 从 001 开始，补零位数按总数自适应。
  2. 阅读顺序：日漫从右往左 => 双页图右半 = 小号，左半 = 大号。
  3. 单页：内容不动，仅重命名，占用一个序列号。
  4. 基准画布：取该册「最高频的双页宽度的一半」作为单幅基准宽 w_base。
     双页判定阈值：宽 / 高 > SINGLE_MAX_RATIO(1.2)。
  5. 硬切：凡判定为多页的图，按 w_base 等分硬切，绝不缩放、绝不裁掉内容。
  6. 超宽图（片数 >= 3）照切，并记入日志提醒。
  7. 输出统一 PNG（无损），输出到新目录，不动原图。

相对旧版的改进：
  - build_plan 的尺寸扫描改为并行读头部（机械盘/网络盘明显更快）；
  - build_plan / execute_plan 都支持 cancel 回调 —— 旧版「停止」按钮在
    单册大任务中实际不生效（只能在册与册之间停），现在可即时中断；
  - 单张写盘失败不再拖垮整册：记入 result.write_errors，最后统一上报。
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image

from .imaging import (IMAGE_EXTS, list_images, read_sizes, slice_bounds,
                      pad_to_width)
from .naming import natural_key, output_name

# 宽/高 超过此值视为「多页拼接图」
SINGLE_MAX_RATIO = 1.2
# 认定为多页时，最少切几片
MIN_SLICES = 2
# 片数达到此值算「超宽图」，记日志提醒
WIDE_WARN_SLICES = 3

Progress = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


@dataclass
class PlanItem:
    """一张输出图（原始文件被切成 N 片之一，或单页原样）"""
    src_path: str
    src_index: int
    slice_index: int       # 在该源文件内的切片序号（0-based，从左到右）
    slice_count: int       # 该源文件总片数（1 = 单页）
    size: tuple[int, int]
    out_width: int
    is_wide: bool = False


@dataclass
class PlanResult:
    items: list[PlanItem] = field(default_factory=list)
    base_width: int = 0
    double_count: int = 0
    single_count: int = 0
    wide_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)        # 尺寸读取失败
    write_errors: list[str] = field(default_factory=list)  # 写盘失败（执行阶段）
    cancelled: bool = False


def detect_base_width(sizes) -> int:
    """
    检测基准宽：取双页图中最高频宽度的一半。
    若没有双页图，取最高频宽度的一半兜底。
    """
    sizes = [s for s in sizes if s]
    if not sizes:
        return 0
    doubles = [w for (w, h) in sizes if h > 0 and w / h > SINGLE_MAX_RATIO]
    pool = doubles if doubles else [w for (w, h) in sizes]
    if not pool:
        return 0
    common_w = Counter(pool).most_common(1)[0][0]
    return max(round(common_w / 2), 1)


def build_plan(folder: str, progress: Progress | None = None,
               cancel: CancelCheck | None = None) -> PlanResult:
    """扫描一册文件夹，产出输出计划（不写盘）。"""
    names = list_images(folder)
    total = len(names)
    result = PlanResult()

    paths = [os.path.join(folder, n) for n in names]
    sizes = read_sizes(paths)  # 并行读头部

    metas: list[tuple[str, tuple[int, int]]] = []
    for i, (n, sz) in enumerate(zip(names, sizes)):
        if cancel and cancel():
            result.cancelled = True
            return result
        if sz is None:
            result.errors.append(f"{n}: 无法读取尺寸")
        else:
            metas.append((n, sz))
        if progress:
            progress(i + 1, total, n)

    if not metas:
        return result

    result.base_width = detect_base_width([s for _, s in metas])

    for idx, (n, (w, h)) in enumerate(metas):
        ratio = w / h if h else 0
        if ratio <= SINGLE_MAX_RATIO:
            result.items.append(PlanItem(
                src_path=os.path.join(folder, n), src_index=idx, slice_index=0,
                slice_count=1, size=(w, h), out_width=w,
            ))
            result.single_count += 1
        else:
            count = (max(MIN_SLICES, round(w / result.base_width))
                     if result.base_width else MIN_SLICES)
            is_wide = count >= WIDE_WARN_SLICES
            bounds = slice_bounds(w, count)
            for sfx in range(count):
                x0, x1 = bounds[sfx]
                result.items.append(PlanItem(
                    src_path=os.path.join(folder, n), src_index=idx,
                    slice_index=sfx, slice_count=count, size=(w, h),
                    out_width=x1 - x0, is_wide=is_wide,
                ))
            result.double_count += 1
            if is_wide:
                result.wide_files.append(f"{n}  ({w}x{h}, {count}片)")

    return result


# ---------------------------------------------------------------- 执行

# PNG 编码底层走 zlib，压缩时释放 GIL —— 线程池即可拿到近线性加速。
MAX_WORKERS = 8


def default_workers(n_jobs: int) -> int:
    """按任务数与 CPU 核数决定线程数；任务太少时不值得开线程池。"""
    cores = os.cpu_count() or 1
    if n_jobs < 3 or cores < 2:
        return 1
    return max(1, min(MAX_WORKERS, cores - 2, n_jobs))


def render_slices(src_path: str, jobs: list[tuple[str, int, int]],
                  base_width: int) -> list[str]:
    """
    打开一张源图，按 jobs 逐块裁剪并写成 PNG。
    jobs: [(out_path, slice_index, slice_count), ...]，调用方已按「从右往左」排好。
    特意设计成「一张源图 = 一个任务」：同一张图只解码一次。
    """
    written: list[str] = []
    with Image.open(src_path) as im:
        im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        bounds = slice_bounds(im.width, jobs[0][2]) if jobs and jobs[0][2] > 1 else None
        for out_path, slice_index, slice_count in jobs:
            if slice_count == 1:
                piece = im
            else:
                x0, x1 = bounds[slice_index]
                piece = im.crop((x0, 0, x1, im.height))
                if piece.width < base_width:
                    piece = pad_to_width(piece, base_width)
            piece.save(out_path, "PNG")
            written.append(out_path)
    return written


def execute_plan(plan: PlanResult, out_dir: str,
                 progress: Progress | None = None,
                 workers: int | None = None,
                 cancel: CancelCheck | None = None) -> list[str]:
    """
    按计划写盘。输出文件名开工前一次性算好，多线程也不会打乱编号。
    cancel() 返回 True 时在任务边界即时中断（已写出的文件保留）。
    """
    os.makedirs(out_dir, exist_ok=True)
    total_out = len(plan.items)

    groups: dict[str, list[PlanItem]] = {}
    order: list[str] = []
    for it in plan.items:
        if it.src_path not in groups:
            groups[it.src_path] = []
            order.append(it.src_path)
        groups[it.src_path].append(it)

    seq = 0
    tasks: list[tuple[str, list[tuple[str, int, int]]]] = []
    for src in order:
        items = sorted(groups[src], key=lambda x: -x.slice_index)  # 从右往左
        jobs: list[tuple[str, int, int]] = []
        for it in items:
            seq += 1
            jobs.append((os.path.join(out_dir, output_name(seq, total_out, ".png")),
                         it.slice_index, it.slice_count))
        tasks.append((src, jobs))

    if workers is None:
        workers = default_workers(len(tasks))

    def _cancelled() -> bool:
        if cancel and cancel():
            plan.cancelled = True
            return True
        return False

    written: list[str] = []
    if workers <= 1:
        for src, jobs in tasks:
            if _cancelled():
                break
            try:
                written.extend(render_slices(src, jobs, plan.base_width))
            except Exception as e:  # noqa: BLE001
                plan.write_errors.append(f"{os.path.basename(src)}: {e}")
            if progress:
                progress(len(written), total_out,
                         os.path.basename(written[-1]) if written else "")
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(render_slices, src, jobs, plan.base_width): src
                       for src, jobs in tasks}
            for fut in as_completed(futures):
                try:
                    written.extend(fut.result())
                except Exception as e:  # noqa: BLE001
                    plan.write_errors.append(
                        f"{os.path.basename(futures[fut])}: {e}")
                if progress:
                    progress(len(written), total_out,
                             os.path.basename(written[-1]) if written else "")
                if _cancelled():
                    for f in futures:
                        f.cancel()
                    break

    written.sort(key=natural_key)
    return [os.path.basename(p) for p in written]


def process_folder(folder: str, out_dir: str | None = None,
                   progress: Progress | None = None,
                   dry_run: bool = False,
                   cancel: CancelCheck | None = None
                   ) -> tuple[PlanResult, list[str]]:
    """一站式处理一册。out_dir 为空时取 <folder>_split。"""
    if out_dir is None:
        out_dir = folder.rstrip("\\/") + "_split"
    plan = build_plan(folder, progress=progress, cancel=cancel)
    if dry_run or plan.cancelled:
        return plan, []
    written = execute_plan(plan, out_dir, progress=progress, cancel=cancel)
    return plan, written
