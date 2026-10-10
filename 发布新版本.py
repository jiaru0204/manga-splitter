# -*- coding: utf-8 -*-
"""
一键发布新版本（在自己电脑上运行）：改版本号 -> 打包 exe -> 建 Release -> 上传附件。

用法：
    python 发布新版本.py 2.0.1 "修复：xx 问题；优化：yy"
    python 发布新版本.py                 # 不带参数：只显示当前版本与用法

之后所有已安装的旧版程序在启动时/点「检查更新」就会看到新版本并自动热替换。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = "jiaru0204/manga-splitter"
INIT = os.path.join(ROOT, "manga_splitter", "__init__.py")
EXE = os.path.join(ROOT, "dist", "漫画双页分割器.exe")


def token() -> str:
    p = os.path.expanduser("~/.git-credentials")
    if not os.path.exists(p):
        sys.exit("未找到 ~/.git-credentials（需要 GitHub token）")
    with open(p, encoding="utf-8") as f:
        for line in f:
            if "github.com" in line:
                return line.strip().split("@")[0].split(":")[-1]
    sys.exit("~/.git-credentials 里没有 github.com 条目")


def api(method: str, path: str, tok: str, payload=None,
        host="api.github.com", raw: bytes | None = None):
    url = f"https://{host}{path}"
    data = raw if raw is not None else (
        json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"token {tok}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "manga-splitter-release",
        "Content-Type": "application/octet-stream" if raw is not None
                        else "application/json",
    })
    with urllib.request.urlopen(req, timeout=600) as r:
        body = r.read()
        return json.loads(body) if body and raw is None else {"ok": True}


def main() -> int:
    if len(sys.argv) < 2:
        src = open(INIT, encoding="utf-8").read()
        cur = re.search(r'__version__ = "([^"]+)"', src).group(1)
        print(f"当前版本：v{cur}")
        print('用法：python 发布新版本.py <新版本号> ["更新说明"]')
        print('例：  python 发布新版本.py 2.0.1 "修复裁剪页滚轮缩放抖动"')
        return 0

    ver = sys.argv[1].lstrip("vV")
    notes = sys.argv[2] if len(sys.argv) > 2 else f"v{ver}"

    # 1) 改版本号
    src = open(INIT, encoding="utf-8").read()
    src = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{ver}"', src)
    open(INIT, "w", encoding="utf-8").write(src)
    print(f"[1/4] 版本号 -> v{ver}")

    # 2) 打包
    print("[2/4] 打包 exe…")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "build_exe.py")], cwd=ROOT)
    if r.returncode != 0 or not os.path.exists(EXE):
        sys.exit("打包失败")
    print(f"      {EXE}  {os.path.getsize(EXE)/1048576:.1f} MB")

    # 3) 建 Release
    tok = token()
    tag = f"v{ver}"
    print(f"[3/4] 创建 Release {tag}…")
    try:
        rel = api("POST", f"/repos/{REPO}/releases", tok, {
            "tag_name": tag, "name": f"{tag}",
            "body": f"## 漫画双页分割器 {tag}\n\n{notes}\n\n"
                    f"下载 **MangaSplitter-{tag}.exe**，双击即用。",
            "draft": False, "prerelease": False,
        })
        rel_id = rel["id"]
    except urllib.error.HTTPError as e:
        if e.code == 422:      # 已存在 -> 复用
            rel = api("GET", f"/repos/{REPO}/releases/tags/{tag}", tok)
            rel_id = rel["id"]
            print("      Release 已存在，复用")
        else:
            raise

    # 4) 上传附件（走 uploads.github.com）
    print("[4/4] 上传附件…")
    with open(EXE, "rb") as f:
        blob = f.read()
    name = f"MangaSplitter-{tag}.exe"
    res = api("POST", f"/repos/{REPO}/releases/{rel_id}/assets?name={name}",
              tok, raw=blob, host="uploads.github.com")
    print(f"✔ 发布完成：https://github.com/{REPO}/releases/tag/{tag}")
    print("  已安装的旧版下次启动即可检测到更新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
