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


def check_latest(repo: str = UPDATE_REPO) -> dict:
    """
    查询最新 release。返回：
      {ok, error?, current, latest?, tag?, notes?, asset_url?, asset_name?, asset_size?}
    ok=False 表示网络/仓库问题（静默失败，不打扰用户）。
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
        return {"ok": False, "error": str(e), "current": __version__}

    tag = data.get("tag_name", "")
    asset_url, asset_name, asset_size = None, None, 0
    for a in data.get("assets", []):
        if (a.get("name") or "").lower().endswith(".exe"):
            asset_url = a.get("browser_download_url")
            asset_name = a.get("name")
            asset_size = a.get("size", 0)
            break

    return {
        "ok": True,
        "current": __version__,
        "tag": tag,
        "latest": is_newer(tag),
        "notes": (data.get("body") or "")[:2000],
        "asset_url": asset_url,
        "asset_name": asset_name,
        "asset_size": asset_size,
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
    """启动时清理上次更新残留的旧 exe（*.old.exe）。"""
    if not getattr(sys, "frozen", False):
        return
    d = os.path.dirname(os.path.abspath(sys.executable))
    try:
        for n in os.listdir(d):
            if n.lower().endswith(".old.exe"):
                try:
                    os.remove(os.path.join(d, n))
                except OSError:
                    pass
    except OSError:
        pass


def apply_update_and_restart(new_exe: str) -> None:
    """
    用热替换应用更新并重启：
      当前 exe -> <原名>.old.exe；新 exe -> 当前 exe 的文件名；启动新进程。
    调用后当前进程应立即退出（由调用方负责）。
    """
    import subprocess
    cur = os.path.abspath(sys.executable)
    old = cur[:-4] + ".old.exe" if cur.lower().endswith(".exe") else cur + ".old.exe"
    if os.path.exists(old):
        try:
            os.remove(old)
        except OSError:
            pass
    os.replace(cur, old)          # 运行中的 exe 可以改名
    os.replace(new_exe, cur)      # 新版本顶上来，保持文件名不变
    subprocess.Popen([cur], cwd=os.path.dirname(cur), close_fds=True)
