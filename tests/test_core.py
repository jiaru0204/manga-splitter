# -*- coding: utf-8 -*-
"""核心层单元测试：不依赖 GUI，直接跑 pytest。"""

import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manga_splitter.core import naming, imaging, splitter, cropper  # noqa: E402


# ------------------------------------------------------------ naming

def test_natural_key():
    assert imaging.list_images  # smoke
    names = ["a-10.png", "a-9.png", "a-1.png", "b.png"]
    assert sorted(names, key=naming.natural_key) == ["a-1.png", "a-9.png", "a-10.png", "b.png"]


def test_extract_volume_number():
    assert naming.extract_volume_number("Lm-dcb Vol_04") == "04"
    assert naming.extract_volume_number("出包王女-愛藏版_01") == "01"
    assert naming.extract_volume_number("没有数字") is None


def test_render_output_name():
    assert naming.render_output_name("Lm-dcb Vol_04", "乱马 典藏版 ##卷") == "乱马 典藏版 04卷"
    assert naming.render_output_name("Vol_4", "###") == "004"
    assert naming.render_output_name("Vol_4", "{n}") == "4"
    assert naming.render_output_name("Vol_4", "{n:02}") == "04"
    assert naming.render_output_name("abc", "##") == "abc"      # 无数字原样
    assert naming.render_output_name("Vol_4", "") == "Vol_4"    # 空模板原样
    # 非法字符剔除
    assert "/" not in naming.render_output_name("Vol_4", "a/b##")


def test_render_output_filename():
    assert naming.render_output_filename("{name}", 1, "", "084.jpg") == "084.png"
    assert naming.render_output_filename("{n:03}{a}", 7, "b", "x.png") == "007b.png"
    assert naming.render_output_filename("{vol}-{n}", 3, "", "x.png", "Vol_04") == "04-3.png"
    # 空结果回退默认模板
    assert naming.render_output_filename("", 1, "", "084.jpg") == "084.png"


def test_suffix_at():
    assert naming.suffix_at(0) == "a"
    assert naming.suffix_at(25) == "z"
    assert naming.suffix_at(26) == "aa"


def test_output_name():
    assert naming.output_name(1, 50) == "001.png"
    assert naming.output_name(7, 1200) == "0007.png"


# ------------------------------------------------------------ imaging

@pytest.fixture()
def img_folder(tmp_path):
    """造一册图：3 张双页(2400x1200) + 2 张单页(1200x1200)。"""
    d = tmp_path / "Vol_01"
    d.mkdir()
    for i in range(3):
        Image.new("RGB", (2400, 1200), (200, 30 + i, 30)).save(d / f"{i+1:03d}.png")
    for i in range(2):
        Image.new("RGB", (1200, 1200), (30, 30, 200)).save(d / f"{i+4:03d}.jpg")
    return str(d)


def test_slice_bounds():
    assert splitter.slice_bounds(100, 2) == [(0, 50), (50, 100)]
    b = splitter.slice_bounds(101, 3)
    assert b[0][0] == 0 and b[-1][1] == 101
    for (x0, x1), (y0, y1) in zip(b, b[1:]):
        assert x1 == y0  # 无缝衔接


def test_pad_to_width():
    im = Image.new("RGB", (100, 50), (1, 2, 3))
    out = imaging.pad_to_width(im, 200)
    assert out.size == (200, 50)
    assert out.getpixel((50, 25)) == (255, 255, 255)   # 左补白
    assert out.getpixel((51, 25)) == (1, 2, 3)
    # 比目标宽时原样
    assert imaging.pad_to_width(im, 80) is im


def test_compose_base_center_and_split_colors():
    piece = Image.new("RGB", (100, 60), (10, 20, 30))
    out = imaging.compose_base(piece, 200, 100, (255, 255, 255))
    assert out.size == (200, 100)
    assert out.getpixel((50, 20)) == (10, 20, 30)      # 内容居中
    assert out.getpixel((5, 5)) == (255, 255, 255)
    # 左右分色 + 左补 20
    out2 = imaging.compose_base(piece, 200, 100, (255, 0, 0), (0, 0, 255),
                                pad_l=20, pad_r=80)
    assert out2.getpixel((10, 50)) == (255, 0, 0)      # 左补红
    assert out2.getpixel((20, 30)) == (10, 20, 30)     # 内容从 x=20 开始
    assert out2.getpixel((150, 50)) == (0, 0, 255)     # 右补蓝


# ------------------------------------------------------------ splitter

def test_build_plan_and_execute(img_folder):
    plan = splitter.build_plan(img_folder)
    assert plan.base_width == 1200
    assert plan.double_count == 3
    assert plan.single_count == 2
    assert len(plan.items) == 3 * 2 + 2

    out_dir = img_folder + "_split"
    written = splitter.execute_plan(plan, out_dir, workers=1)
    assert len(written) == 8
    # 命名 001..008，全部 PNG
    assert written[0] == "001.png" and written[-1] == "008.png"
    # 双页切成左右两半，单页原样
    assert Image.open(os.path.join(out_dir, "001.png")).size == (1200, 1200)
    # 从右往左：001 是右半、002 是左半
    # 源图 3 张双页 -> 001..006，随后 007/008 是两张单页
    assert Image.open(os.path.join(out_dir, "007.png")).size == (1200, 1200)


def test_plan_cancel(img_folder):
    plan = splitter.build_plan(img_folder, cancel=lambda: True)
    assert plan.cancelled


def test_execute_cancel_midway(img_folder):
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 1   # 第一个任务后取消

    plan = splitter.build_plan(img_folder)
    written = splitter.execute_plan(plan, img_folder + "_split2",
                                    workers=1, cancel=cancel)
    assert plan.cancelled
    assert 0 < len(written) < 8


def test_detect_base_width_no_doubles(tmp_path):
    assert splitter.detect_base_width([(1000, 1500), (1000, 1500)]) == 500
    assert splitter.detect_base_width([]) == 0


# ------------------------------------------------------------ cropper

def test_cropspec_clamp():
    s = cropper.CropSpec(50, 50, 200, 200).clamp_to(120, 120)
    assert (s.x, s.y, s.w, s.h) == (0, 0, 120, 120)


def test_seq_assigner():
    a = cropper.SeqAssigner()
    assert a.seq_num(5) == 1
    assert a.seq_num(2) == 2
    assert a.seq_num(5) == 1          # 复用
    assert a.next_suffix(5) == "a"
    assert a.next_suffix(5) == "b"
    st = a.snapshot()
    b = cropper.SeqAssigner()
    b.restore(st)
    assert b.assign == a.assign and b.next_seq == a.next_seq


def test_render_crop_with_rotation_and_base(img_folder, tmp_path):
    src = os.path.join(img_folder, "001.png")   # 2400x1200
    out = str(tmp_path / "out.png")
    base = cropper.BaseConfig(enabled=True, bw=800, bh=1000,
                              left_rgb=(255, 255, 255))
    w, h, fits = cropper.render_crop(
        src, cropper.CropSpec(0, 0, 700, 900), 90, base, out)
    # 旋转 90° 后源图变为 1200x2400，裁 700x900，贴到 800x1000
    assert (w, h) == (800, 1000) and fits
    assert Image.open(out).size == (800, 1000)


def test_batch_crop_reports_errors(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    Image.new("RGB", (400, 300)).save(d / "001.png")
    (d / "002.png").write_bytes(b"not an image")   # 损坏文件
    todo = [(0, str(d / "001.png")), (1, str(d / "002.png"))]
    outs = {0: str(tmp_path / "o1.png"), 1: str(tmp_path / "o2.png")}
    res = cropper.batch_crop(todo, (200, 200), lambda i: 0,
                             lambda i: outs[i], cropper.BaseConfig())
    assert res.done == 1
    assert res.failed == 1
    assert len(res.errors) == 1          # 旧版这里会静默吞掉


def test_batch_add_base(tmp_path):
    src = tmp_path / "done"
    src.mkdir()
    Image.new("RGB", (100, 100)).save(src / "a.png")
    Image.new("RGB", (9999, 10)).save(src / "b.png")   # 比基底大 → 跳过
    base = cropper.BaseConfig(enabled=True, bw=500, bh=500)
    res = cropper.batch_add_base(str(src), str(src) + "_base", base)
    assert res.done == 1 and res.skipped_base == 1
    assert Image.open(str(src) + "_base" + os.sep + "a.png").size == (500, 500)
