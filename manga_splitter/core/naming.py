# -*- coding: utf-8 -*-
"""
命名与排序：卷号提取、命名模板渲染、自然排序。

从旧 splitter_core.py 原样提炼（行为保持一致，有完整注释），
仅修复了 `natural_key` 在函数内重复 import re 的小问题。
"""

from __future__ import annotations

import os
import re

# Windows 文件名非法字符
_ILLEGAL_CHARS = r'\/:*?"<>|'

# 裁剪输出文件名的默认模板：直接用「当前图片的名称」
DEFAULT_NAME_TPL = "{name}"


def natural_key(name: str):
    """自然排序键：'a-10' > 'a-9'。"""
    parts = re.split(r"(\d+)", name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def extract_volume_number(name: str) -> str | None:
    """
    从文件夹名里提取卷号：取最后一段数字。
      'Lm-dcb Vol_04'     -> '04'
      '出包王女-愛藏版_01' -> '01'
    没有数字则返回 None。
    """
    nums = re.findall(r"\d+", name)
    return nums[-1] if nums else None


def strip_illegal(text: str) -> str:
    """剔除文件名非法字符。"""
    return "".join(ch for ch in text if ch not in _ILLEGAL_CHARS).strip()


def render_output_name(folder_base: str, template: str | None) -> str:
    """
    按「命名模板」渲染输出名称（文件夹名 / 文件名都可用）。

    模板占位符：
      - 连续 #  ：卷号，个数 = 补零位数（'##' -> 04）
      - {n}     ：卷号，不补零
      - {n:02}  ：卷号，补零到 2 位
    模板为空、或文件夹名里没有数字时，原样返回 folder_base。
    """
    if not template:
        return folder_base
    num = extract_volume_number(folder_base)
    if num is None:
        return folder_base

    try:
        ival: int | None = int(num)
    except ValueError:
        ival = None

    def _hash(m: "re.Match") -> str:
        width = len(m.group(0))
        if ival is None:
            return num
        s = str(ival)
        return s.zfill(width) if len(s) < width else s

    out = re.sub(r"#+", _hash, template)

    def _brace(m: "re.Match") -> str:
        w = m.group(1)
        if ival is None:
            return num
        return str(ival).zfill(int(w)) if w else str(ival)

    out = re.sub(r"\{n(?::0?(\d+))?\}", _brace, out)
    out = strip_illegal(out)
    return out or folder_base


def render_output_filename(template: str, seq: int, block: str,
                           src_name: str, folder_name: str = "",
                           ext: str = ".png") -> str:
    """
    按模板渲染「裁剪输出」的文件名（不含目录）。

    占位符：
      {n} / {n:3} / {n:03}  输出序号（可补零）
      {a}                   同图多块后缀 a / b / c …（只有一块时为空）
      {name}                源图片文件名（不含扩展名）
      {vol}                 卷号（文件夹名里最后一段数字，没有则为空）

    模板为空、或渲染结果为空时，回退到 DEFAULT_NAME_TPL。
    """
    src_stem = os.path.splitext(src_name)[0]
    vol = (extract_volume_number(folder_name) or "") if folder_name else ""
    tpl = (template or "").strip() or DEFAULT_NAME_TPL

    def _num(m: "re.Match") -> str:
        width = m.group(1)
        if width:
            try:
                return str(int(seq)).zfill(int(width))
            except ValueError:
                pass
        return str(seq)

    out = re.sub(r"\{n(?::0?(\d+))?\}", _num, tpl)
    out = out.replace("{a}", block)
    out = out.replace("{name}", src_stem)
    out = out.replace("{vol}", vol)
    out = strip_illegal(out)
    if not out:
        return render_output_filename(DEFAULT_NAME_TPL, seq, block,
                                      src_name, folder_name, ext)
    return out + ext


def output_name(seq: int, total: int, ext: str = ".png") -> str:
    """纯数字命名，从 1 开始，补零位数按总数自适应（至少 3 位）。"""
    digits = max(3, len(str(total)))
    return f"{seq:0{digits}d}{ext}"


def suffix_at(n: int) -> str:
    """第 n 块的后缀：0->a, 1->b, ... 25->z, 26->aa。"""
    idx = n
    s = ""
    while True:
        s = chr(ord("a") + idx % 26) + s
        idx = idx // 26 - 1
        if idx < 0:
            break
    return s
