# -*- coding: utf-8 -*-
"""
基准：旧 splitter_core vs 新 core.splitter，同一合成图集，各跑 3 轮取最优。
图集：40 张双页(2600x1900) + 10 张单页(1300x1900)，JPEG 源（贴近真实漫画）。
"""
import os, sys, time, shutil, tempfile

sys.path.insert(0, r"D:\漫画分页\page_splitter")
sys.path.insert(0, r"D:\漫画分页")

from PIL import Image
import splitter_core as old
from manga_splitter.core import splitter as new
from manga_splitter.core import cropper, imaging

def make_set(root, n_double=40, n_single=10):
    os.makedirs(root, exist_ok=True)
    for i in range(n_double):
        Image.new("RGB", (2600, 1900), (i * 5 % 255, 80, 160)).save(
            os.path.join(root, f"{i+1:03d}.jpg"), quality=88)
    for i in range(n_single):
        Image.new("RGB", (1300, 1900), (60, i * 20 % 255, 90)).save(
            os.path.join(root, f"{n_double+i+1:03d}.jpg"), quality=88)

def timeit(fn, rounds=3):
    best = float("inf")
    for _ in range(rounds):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best

def main():
    tmp = tempfile.mkdtemp(prefix="manga_bench_")
    src = os.path.join(tmp, "Vol_01")
    make_set(src)
    n_imgs = len(os.listdir(src))
    print(f"图集: {n_imgs} 张 (40 双页 2600x1900 + 10 单页 1300x1900, JPEG)")

    # ---------- 计划扫描 ----------
    t_old_plan = timeit(lambda: old.build_plan(src))
    t_new_plan = timeit(lambda: new.build_plan(src))

    # ---------- 分割执行 ----------
    def run_old():
        p = old.build_plan(src)
        old.execute_plan(p, os.path.join(tmp, "o_old"), workers=8)
        shutil.rmtree(os.path.join(tmp, "o_old"))

    def run_new():
        p = new.build_plan(src)
        new.execute_plan(p, os.path.join(tmp, "o_new"), workers=8)
        shutil.rmtree(os.path.join(tmp, "o_new"))

    t_old_exec = timeit(run_old, rounds=2)
    t_new_exec = timeit(run_new, rounds=2)
    out_imgs = 40 * 2 + 10

    # ---------- 批量裁剪 ----------
    def old_batch():
        old.batch_crop(src, os.path.join(tmp, "c_old"), 1300, 1900)
        shutil.rmtree(os.path.join(tmp, "c_old"))

    todo = list(enumerate(os.path.join(src, n) for n in imaging.list_images(src)))
    outs = {i: os.path.join(tmp, "c_new", f"{i:03d}.png") for i, _ in todo}

    def new_batch():
        cropper.batch_crop(todo, (1300, 1900), lambda i: 0,
                           lambda i: outs[i], cropper.BaseConfig())
        shutil.rmtree(os.path.join(tmp, "c_new"))

    t_old_crop = timeit(old_batch, rounds=2)
    t_new_crop = timeit(new_batch, rounds=2)

    def row(name, to, tn, n):
        print(f"{name:<14} {to*1000:>9.0f} ms  {tn*1000:>9.0f} ms  "
              f"{to/tn:>6.2f}x   {n/to:>6.1f} → {n/tn:.1f} 张/秒")

    print(f"\n{'阶段':<14} {'旧版':>10}   {'新版':>10}   {'加速':>6}   吞吐(旧→新)")
    row("计划扫描", t_old_plan, t_new_plan, n_imgs)
    row("分割执行", t_old_exec, t_new_exec, out_imgs)
    row("批量裁剪", t_old_crop, t_new_crop, n_imgs)

    shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
