# -*- coding: utf-8 -*-
"""
自动更新（GitHub Releases）。

机制（onefile exe 适用，无需额外 updater 程序）：
  1. 查询 api.github.com 上最新 release，比较版本号；
  2. 后台下载新 exe 到程序目录的 .update_tmp/；
  3. 应用更新：Windows 允许**重命名**正在运行的 exe（不能删，但能改名）——
     把当前 exe 改名成 <名字>.old.exe，把新 exe 改名成原文件名，
     启动新进程后退出当前进程；
  4. 下次启动时清理残留的 *.old.exe。

版本比较只做「不同即提示」，由 release tag（v2.0.1 等）决定，
不做复杂的 semver 大小判断 —— 避免旧版本号「回滚」判断出错。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error

from . import __version__, UPDATE_REPO
from .settings import app_dir

UA = {"User-Agent": "manga-splitter-updater"}
TIMEOUT = 15


class UpdateError(Exception):
    pass


def _version_tuple(v: str) -> tuple:
    v = v.lstrip("vV")
    parts = []
    for seg in v.split("."):
        num = "".join(ch for ch in seg if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def is_newer(remote_tag: str, local: str = __version__) -> bool:
    """remote_tag 的版本号是否高于本地版本。"""
    try:
        return _version_tuple(remote_tag) > _version_tuple(local)
    except Exception:  # noqa: BLE001
        return False


def is_onedir() -> bool:
    """当前是否为「文件夹版」（onedir）—— 特征是 exe 旁边有 _internal 目录。"""
    return os.path.isdir(os.path.join(app_dir(), "_internal"))


def check_latest(repo: str = UPDATE_REPO) -> dict:
    """
    查询最新 release。返回：
      {ok, error?, current, latest?, tag?, notes?, asset_url?, asset_name?,
       asset_size?, onedir?}
    ok=False 表示网络/仓库问题（静默失败，不打扰用户）。

    附件选择：文件夹版优先取 .zip（含 _internal），单文件版取 .exe。
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
        return {"ok": False, "error": str(e), "current": __version__}

    tag = data.get("tag_name", "")
    want = ".zip" if is_onedir() else ".exe"
    assets = data.get("assets", [])
    pick = None
    for a in assets:                       # 先找匹配当前形态的
        if (a.get("name") or "").lower().endswith(want):
            pick = a
            break
    if pick is None:                       # 退而求其次
        for a in assets:
            n = (a.get("name") or "").lower()
            if n.endswith(".zip") or n.endswith(".exe"):
                pick = a
                break

    return {
        "ok": True,
        "current": __version__,
        "tag": tag,
        "latest": is_newer(tag),
        "notes": (data.get("body") or "")[:2000],
        "asset_url": pick.get("browser_download_url") if pick else None,
        "asset_name": pick.get("name") if pick else None,
        "asset_size": pick.get("size", 0) if pick else 0,
        "onedir": is_onedir(),
    }


def download_asset(url: str, dst: str,
                   progress=None, cancel=None) -> str:
    """
    下载 exe 到 dst（流式，64KB 块）。
    progress(received, total)；cancel() 返回 True 时中断并删除半成品。
    """
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    req = urllib.request.Request(url, headers=UA)
    tmp = dst + ".part"
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r, \
             open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                if cancel and cancel():
                    raise UpdateError("已取消")
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
        os.replace(tmp, dst)
        return dst
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def cleanup_old_versions():
    """
    启动时清理上次更新残留：
      - 单文件版：<名字>.old.exe
      - 文件夹版：同级目录里的 <名字>.old 目录、<名字>.new 目录
    """
    if not getattr(sys, "frozen", False):
        return
    d = os.path.dirname(os.path.abspath(sys.executable))
    try:
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if n.lower().endswith(".old.exe"):
                try:
                    os.remove(p)
                except OSError:
                    pass
        parent = os.path.dirname(d)
        name = os.path.basename(d)
        for suffix in (".old", ".new"):
            p = os.path.join(parent, name + suffix)
            if os.path.isdir(p):
                import shutil
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def apply_update_and_restart(new_file: str) -> None:
    """
    应用更新并重启。调用后当前进程应立即退出（由调用方负责）。

    单文件版：Windows 允许重命名运行中的 exe —— 当前 exe 改名 .old.exe，
              新 exe 顶上来，再启动新进程。
    文件夹版：把 zip 解到 <名字>.new/，由**新版本自己**启动一个带
              --apply-update 的进程去替换目录（它不在被占用的目录里运行，
              所以能安全地改名/移动旧目录），随后当前进程退出。
    """
    import subprocess
    exe = os.path.abspath(sys.executable)

    if is_onedir():
        import shutil
        import zipfile
        app_d = app_dir()
        parent = os.path.dirname(app_d)
        name = os.path.basename(app_d)
        new_dir = os.path.join(parent, name + ".new")
        shutil.rmtree(new_dir, ignore_errors=True)
        os.makedirs(new_dir, exist_ok=True)
        with zipfile.ZipFile(new_file) as z:
            z.extractall(new_dir)
        helper = os.path.join(new_dir, os.path.basename(exe))
        if not os.path.exists(helper):      # zip 里多套了一层同名文件夹
            for n in os.listdir(new_dir):
                cand = os.path.join(new_dir, n, os.path.basename(exe))
                if os.path.exists(cand):
                    helper = cand
                    break
        subprocess.Popen([helper, "--apply-update", app_d, os.path.dirname(helper)],
                         cwd=os.path.dirname(helper), close_fds=True)
        return

    old = exe[:-4] + ".old.exe" if exe.lower().endswith(".exe") else exe + ".old.exe"
    if os.path.exists(old):
        try:
            os.remove(old)
        except OSError:
            pass
    os.replace(exe, old)          # 运行中的 exe 可以改名
    os.replace(new_file, exe)     # 新版本顶上来，保持文件名不变
    subprocess.Popen([exe], cwd=os.path.dirname(exe), close_fds=True)


def run_apply_update_helper(old_dir: str, new_dir: str) -> int:
    """
    在「新版本目录」里运行的替换助手：
      1. 等旧进程退出（旧 exe 文件锁释放）；
      2. 旧目录改名 <名字>.old；
      3. 新目录搬到旧目录的位置；
      4. 启动正主，删除残留旧目录。
    """
    import shutil
    import subprocess
    import time

    parent = os.path.dirname(os.path.abspath(old_dir))
    name = os.path.basename(os.path.abspath(old_dir))
    exe_name = os.path.basename(sys.executable)
    old_backup = os.path.join(parent, name + ".old")

    # 等旧进程放手
    for _ in range(150):
        try:
            os.rename(old_dir, old_dir)     # 能改名说明没被占用
            break
        except OSError:
            time.sleep(0.2)
    else:
        return 1

    shutil.rmtree(old_backup, ignore_errors=True)
    try:
        os.rename(old_dir, old_backup)
    except OSError:
        return 2
    try:
        shutil.move(os.path.abspath(new_dir), old_dir)
    except Exception:  # noqa: BLE001
        os.rename(old_backup, old_dir)      # 回滚
        return 3

    subprocess.Popen([os.path.join(old_dir, exe_name)], cwd=old_dir, close_fds=True)
    time.sleep(1.0)
    shutil.rmtree(old_backup, ignore_errors=True)
    return 0
